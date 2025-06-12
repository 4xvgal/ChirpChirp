# receiver.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import logging
import os
import time
import json
import datetime
import serial
import struct
import binascii
from typing import List, Optional, Dict, Any

try:
    import decoder
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. decoder.py가 같은 폴더에 있는지 확인하세요.")
    exit(1)

try:
    import rx_logger
except ImportError as e:
    class DummyRxLogger:
        def log_rx_event(*args, **kwargs): pass
    rx_logger = DummyRxLogger()
    print("경고: rx_logger 임포트 실패. CSV 이벤트 로깅이 비활성화됩니다.")

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0"
BAUD         = 9600
SENDER_COMPRESSION_METHOD = "none"

SERIAL_READ_TIMEOUT = 0.05
INITIAL_SYN_TIMEOUT = 7
SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55
ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00
EXPECTED_TOTAL_PACKETS = 200
RE_HANDSHAKE_THRESHOLD = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

VALID_DATA_PKT_LENGTH_RANGE = range(2, 58)
logger.info(f"사용 압축 방식: {SENDER_COMPRESSION_METHOD}")
logger.info(f"수신 유효 데이터 프레임 콘텐츠 길이 범위(LENGTH 바이트 값): {list(VALID_DATA_PKT_LENGTH_RANGE)}")

KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST]
DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
    if not data_bytes: return "<empty>"
    hex_str = binascii.hexlify(data_bytes).decode('ascii')
    lines: List[str] = []
    for i in range(0, len(hex_str), bytes_per_line * 2):
        chunk = hex_str[i:i + bytes_per_line * 2]
        spaced = ' '.join(chunk[j:j+2] for j in range(0, len(chunk), 2))
        lines.append(spaced)
    return "\n  ".join(lines)

def _log_json(payload: dict, meta: dict):
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({"ts_recv_utc": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z", "data": payload, "meta": meta}, ensure_ascii=False) + "\n")

def _send_control_response(s: serial.Serial, seq: int, ack_type: int) -> bool:
    ack_bytes = struct.pack("!BB", ack_type, seq)
    ack_type_hex_str = f"0x{ack_type:02x}"
    type_name_for_log_msg = {ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK", ACK_TYPE_DATA: "DATA_ACK", ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"}.get(ack_type, f"UNKNOWN_TYPE_{ack_type_hex_str}")
    try:
        written = s.write(ack_bytes)
        s.flush()
        logger.info(f"CTRL RSP TX: TYPE={type_name_for_log_msg} ({ack_type_hex_str}), SEQ=0x{seq:02x}")
        return written == len(ack_bytes)
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (TYPE=0x{ack_type:02x}, SEQ=0x{seq:02x}): {e}")
        return False

def receive_loop():
    ser: Optional[serial.Serial] = None
    try:
        logger.info(f"시리얼 포트 {PORT} (Baud: {BAUD}) 열기 시도...")
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = 0.02
        logger.info(f"시리얼 포트 {PORT} 열기 성공.")
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패 ({PORT}): {e}")
        return

    received_message_count = 0
    
    while True: # 메인 세션 루프
        handshake_success = False
        while not handshake_success:
            logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout}s)...")
            try:
                if ser.in_waiting > 0: ser.reset_input_buffer()
                line = ser.readline()
                if line == SYN_MSG:
                    logger.info(f"SYN 수신, 핸드셰이크 ACK 전송")
                    if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                        handshake_success = True
                        logger.info("핸드셰이크 성공.")
                        break
                    else:
                        logger.error("핸드셰이크 ACK 전송 실패. 1초 후 재시도...")
                        time.sleep(1)
                elif not line:
                    logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
                else:
                    logger.debug(f"핸드셰이크: 예상치 않은 데이터 수신 (무시됨): {line!r}")
            except Exception as e_hs:
                logger.error(f"핸드셰이크 중 오류: {e_hs}. 1초 후 재시도...", exc_info=True)
                time.sleep(1)

        ser.timeout = SERIAL_READ_TIMEOUT
        logger.info(f"핸드셰이크 완료. 데이터 수신 대기 중...")
        
        unexpected_syn_counter = 0

        # 데이터 수신 루프
        while True:
            first_byte_data = ser.read(1)
            if not first_byte_data:
                continue

            first_byte_val = first_byte_data[0]
            re_handshake_needed = False

            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER or first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                # 유효한 패킷이므로 카운터 리셋
                unexpected_syn_counter = 0
                
                # 기존 패킷 처리 로직 (분리하지 않고 그대로 둠)
                if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                    packet_type = first_byte_val
                    sequence_byte_data = ser.read(1)
                    if sequence_byte_data:
                        sequence_num = sequence_byte_data[0]
                        type_name_str = "QUERY_SEND_REQUEST" if packet_type == QUERY_TYPE_SEND_REQUEST else f"UNKNOWN_CTRL_0x{packet_type:02x}"
                        logger.info(f"제어 패킷 수신: TYPE={type_name_str}, SEQ=0x{sequence_num:02x}")
                        if packet_type == QUERY_TYPE_SEND_REQUEST:
                            _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                    continue

                elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                    actual_content_len = first_byte_val
                    actual_content_bytes = ser.read(actual_content_len)
                    
                    if len(actual_content_bytes) == actual_content_len:
                        rssi_byte_data = ser.read(1) # RSSI 읽기 시도
                        actual_seq = actual_content_bytes[0]
                        payload_chunk = actual_content_bytes[1:]
                        
                        logger.info(f"데이터 프레임 수신: LENGTH={actual_content_len}B, SEQ=0x{actual_seq:02x}, PAYLOAD_LEN={len(payload_chunk)}B")
                        _send_control_response(ser, actual_seq, ACK_TYPE_DATA)
                        
                        try:
                            payload_dict = decoder.decode(payload_chunk, method=SENDER_COMPRESSION_METHOD)
                            if payload_dict:
                                received_message_count += 1
                                logger.info(f"--- 메시지 #{received_message_count} (SEQ: 0x{actual_seq:02x}) 디코딩 성공 ---")
                                _log_json(payload_dict, {"recv_frame_seq": actual_seq})
                            else:
                                logger.error(f"메시지 (SEQ: 0x{actual_seq:02x}): 디코딩 실패.")
                        except Exception as e_decode:
                            logger.error(f"디코딩 중 오류 (SEQ: 0x{actual_seq:02x}): {e_decode}", exc_info=True)
                    continue
            else:
                # 알 수 없는 바이트 처리 및 SYN 감지
                if first_byte_val == SYN_MSG[0]:
                    rest_of_syn = ser.read(len(SYN_MSG) - 1)
                    full_message = first_byte_data + rest_of_syn
                    
                    if full_message == SYN_MSG:
                        # --- [핵심 수정] SYN 수신 시 즉시 ACK 응답 ---
                        logger.warning(f"데이터 수신 중 예기치 않은 SYN 수신. ACK로 응답합니다.")
                        _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE)
                        # --- 수정 끝 ---
                        
                        unexpected_syn_counter += 1
                        logger.warning(f"연속적인 비정상 SYN 수신 카운트: {unexpected_syn_counter}회.")
                        
                        if unexpected_syn_counter >= RE_HANDSHAKE_THRESHOLD:
                            logger.error(f"재-핸드셰이크 임계값({RE_HANDSHAKE_THRESHOLD}회) 도달. 핸드셰이크 모드로 복귀합니다.")
                            re_handshake_needed = True
                    else:
                        unexpected_syn_counter = 0 # SYN 메시지가 아니었으므로 카운터 초기화
                        logger.debug(f"알 수 없는 데이터 수신 (S로 시작했으나 SYN 아님): {full_message!r}")
                else:
                    unexpected_syn_counter = 0 # 'S'로 시작하지도 않았으므로 카운터 초기화
                    logger.debug(f"알 수 없는 바이트 수신: 0x{first_byte_val:02x}")
            
            if re_handshake_needed:
                break # 데이터 수신 루프를 중단하고 메인 세션 루프로 돌아감

        if not re_handshake_needed:
            break # 정상적으로 루프가 종료된 경우 (예: KeyboardInterrupt)
    
    # 프로그램 종료 로직
    logger.info("수신 프로그램 종료 중...")
    if ser and ser.is_open:
        ser.close()
        logger.info("시리얼 포트 닫힘")

    logger.info(f"--- 최종 수신 결과 ---")
    logger.info(f"  성공적으로 수신/디코딩된 패킷 수: {received_message_count}")
    if EXPECTED_TOTAL_PACKETS > 0:
        pdr = (received_message_count / EXPECTED_TOTAL_PACKETS) * 100
        logger.info(f"  PDR ({received_message_count}/{EXPECTED_TOTAL_PACKETS}): {pdr:.2f}%")


if __name__ == "__main__":
    try:
        logging.getLogger().setLevel(logging.INFO)
        receive_loop()
    except KeyboardInterrupt:
        logger.info("\n수신 중단 (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"치명적인 오류 발생: {e}", exc_info=True)
    finally:
        logger.info("프로그램이 종료되었습니다.")