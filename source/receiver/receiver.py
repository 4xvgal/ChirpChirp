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
    # 변경된 함수 이름 임포트
    from decoder import decode_frame_payload
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. decoder.py가 같은 폴더에 있는지 확인하세요.")
    exit(1)

# --- RX 로거 임포트 ---
try:
    import rx_logger # 같은 폴더의 rx_logger.py 임포트
except ImportError as e:
    print(f"RX 로거 모듈 임포트 실패: {e}. rx_logger.py가 receiver.py와 같은 폴더에 있는지 확인하세요.")
    class DummyRxLogger:
        def log_rx_event(*args, **kwargs):
            pass
    rx_logger = DummyRxLogger()
    print("경고: rx_logger 임포트 실패. CSV 이벤트 로깅이 비활성화됩니다.")
# --- RX 로거 임포트 끝 ---

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0"
BAUD         = 9600

SERIAL_READ_TIMEOUT = 0.05
INITIAL_SYN_TIMEOUT = 7

SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55

ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00

# PDR 계산을 위한 기대 총 패킷 수
EXPECTED_TOTAL_PACKETS = 200

# --- 로거 초기화 ---
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

# --- VALID_DATA_PKT_LENGTH_RANGE 정의 (압축이 없으므로 길이가 고정됨) ---
# encoder._FMT의 크기는 struct.calcsize("<Ihhhhhhhhhffh") = 34바이트 입니다.
# 여기에 시퀀스 번호 1바이트를 더하면 35바이트가 됩니다.
# 이는 'none' 모드일 때의 고정 길이입니다.
# BAM은 길이가 달라질 수 있으므로, 범위를 넓게 잡습니다.
MIN_PAYLOAD_LEN = 10 # 최소 페이로드 길이 (임의의 값, BAM 구현에 따라 조정)
MAX_PAYLOAD_LEN = 56 # encoder.MAX_FRAME_CONTENT_SIZE - 1 (SEQ 바이트 제외)
VALID_DATA_PKT_LENGTH_RANGE = range(1 + MIN_PAYLOAD_LEN, 1 + MAX_PAYLOAD_LEN + 1)
logger.info(f"수신 유효 데이터 프레임 콘텐츠 길이 범위(LENGTH 바이트 값): {list(VALID_DATA_PKT_LENGTH_RANGE)}")
# --- VALID_DATA_PKT_LENGTH_RANGE 정의 끝 ---

KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST] # 0x50

DATA_DIR = "data/raw"
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
        fp.write(json.dumps({
            "ts_recv_utc": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def _send_control_response(s: serial.Serial, seq: int, ack_type: int) -> bool:
    ack_bytes = struct.pack("!BB", ack_type, seq)
    ack_type_hex_str = f"0x{ack_type:02x}"
    type_name_for_log_msg = {
        ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK",
        ACK_TYPE_DATA: "DATA_ACK",
        ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"
    }.get(ack_type, f"UNKNOWN_TYPE_0x{ack_type:02x}")

    try:
        written = s.write(ack_bytes)
        s.flush()
        logger.info(f"CTRL RSP TX: TYPE={type_name_for_log_msg} ({ack_type_hex_str}), SEQ=0x{seq:02x}")
        if logger.isEnabledFor(logging.DEBUG):
             logger.debug(f"  데이터: {bytes_to_hex_pretty_str(ack_bytes)}")
        
        if written == len(ack_bytes):
            rx_logger.log_rx_event(event_type=f"{type_name_for_log_msg}_SENT_OK", ack_seq_sent=seq, ack_type_sent_hex=ack_type_hex_str)
            return True
        else:
            rx_logger.log_rx_event(event_type=f"{type_name_for_log_msg}_SENT_FAIL_PARTIAL", ack_seq_sent=seq, ack_type_sent_hex=ack_type_hex_str, notes=f"Sent {written}/{len(ack_bytes)} bytes")
            return False
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (TYPE=0x{ack_type:02x}, SEQ=0x{seq:02x}): {e}")
        rx_logger.log_rx_event(event_type=f"{type_name_for_log_msg}_SENT_FAIL_EXCEPTION", ack_seq_sent=seq, ack_type_sent_hex=ack_type_hex_str, notes=str(e))
        return False

def receive_loop():
    ser: Optional[serial.Serial] = None
    try:
        logger.info(f"시리얼 포트 {PORT} (Baud: {BAUD}) 열기 시도...")
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = 0.02
        logger.info(f"시리얼 포트 {PORT} 열기 성공. (timeout={ser.timeout}s, inter_byte_timeout={ser.inter_byte_timeout}s)")
        rx_logger.log_rx_event(event_type="SERIAL_PORT_OPEN_SUCCESS", notes=f"Port: {PORT}, Baud: {BAUD}")

    except serial.SerialException as e:
        logger.error(f"포트 열기 실패 ({PORT}): {e}")
        rx_logger.log_rx_event(event_type="SERIAL_PORT_OPEN_FAIL", notes=f"Port: {PORT}, Error: {e}")
        return

    # --- 핸드셰이크 루프 ---
    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout}s)...")
        try:
            if ser.in_waiting > 0:
                ser.reset_input_buffer()
                logger.debug("핸드셰이크 시도 전 입력 버퍼 초기화됨.")
            
            line = ser.readline()
            
            if line == SYN_MSG:
                logger.info(f"SYN 수신, 핸드셰이크 ACK (TYPE={ACK_TYPE_HANDSHAKE:#02x}, SEQ={HANDSHAKE_ACK_SEQ:#02x}) 전송")
                rx_logger.log_rx_event(event_type="HANDSHAKE_SYN_RECV", packet_type_recv_hex="SYN")
                if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                    handshake_success = True
                    logger.info("핸드셰이크 성공.")
                    rx_logger.log_rx_event(event_type="HANDSHAKE_SUCCESS")
                    break 
                else:
                    logger.error("핸드셰이크 ACK 전송 실패. 1초 후 재시도...")
                    time.sleep(1)
            elif not line: 
                logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
                rx_logger.log_rx_event(event_type="HANDSHAKE_SYN_TIMEOUT")
            else: 
                logger.debug(f"핸드셰이크: SYN 대신 예상치 않은 데이터 수신 (무시됨): {line!r}")
        except Exception as e_hs:
            logger.error(f"핸드셰이크 중 오류: {e_hs}. 1초 후 재시도...", exc_info=True)
            rx_logger.log_rx_event(event_type="HANDSHAKE_EXCEPTION", notes=str(e_hs))
            time.sleep(1)

    if not handshake_success:
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        rx_logger.log_rx_event(event_type="HANDSHAKE_FINAL_FAIL")
        if ser and ser.is_open: ser.close()
        return

    # --- 메인 수신 루프 ---
    ser.timeout = SERIAL_READ_TIMEOUT
    logger.info(f"핸드셰이크 완료. 데이터 수신 대기 중... (timeout={ser.timeout}s)")
    
    received_message_count = 0
    expected_total_packets_for_pdr = EXPECTED_TOTAL_PACKETS
    
    try:
        while True:
            first_byte_data = ser.read(1)
            if not first_byte_data: continue
            
            first_byte_val = first_byte_data[0]
            first_byte_hex_str = f"0x{first_byte_val:02x}"

            # --- 제어 패킷 처리 ---
            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                sequence_byte_data = ser.read(1)
                if sequence_byte_data:
                    sequence_num = sequence_byte_data[0]
                    type_name_str = "QUERY_SEND_REQUEST" if first_byte_val == QUERY_TYPE_SEND_REQUEST else f"UNKNOWN_CTRL_{first_byte_hex_str}"
                    logger.info(f"제어 패킷 수신: TYPE={type_name_str}, SEQ=0x{sequence_num:02x}")
                    rx_logger.log_rx_event(event_type="CTRL_PKT_RECV", packet_type_recv_hex=first_byte_hex_str, frame_seq_recv=sequence_num)

                    if first_byte_val == QUERY_TYPE_SEND_REQUEST:
                        _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                else:
                    logger.warning(f"제어 패킷의 시퀀스 번호 수신 실패 (TYPE={first_byte_hex_str}).")
                    rx_logger.log_rx_event(event_type="CTRL_PKT_INCOMPLETE_SEQ", packet_type_recv_hex=first_byte_hex_str)
                continue 
            
            # --- 데이터 패킷 처리 ---
            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                content_len = first_byte_val
                logger.debug(f"데이터 패킷 길이(LENGTH) 바이트 수신: {content_len} ({first_byte_hex_str})")
                rx_logger.log_rx_event(event_type="DATA_LEN_BYTE_RECV", data_len_byte_value=content_len)

                content_bytes = ser.read(content_len)
                
                # RSSI 읽기
                rssi_raw, rssi_dbm = None, None
                if len(content_bytes) == content_len:
                    rssi_byte = ser.read(1)
                    if rssi_byte:
                        rssi_raw = rssi_byte[0]
                        rssi_dbm = -(256 - rssi_raw)
                
                # 데이터 프레임 내용 검증 및 처리
                if len(content_bytes) == content_len:
                    frame_seq = content_bytes[0]
                    payload_chunk = content_bytes[1:]
                    
                    rssi_info_str = f", RSSI={rssi_dbm}dBm" if rssi_dbm is not None else ""
                    logger.info(f"데이터 프레임 수신: LENGTH={content_len}B, FRAME_SEQ=0x{frame_seq:02x}, PAYLOAD_LEN={len(payload_chunk)}B{rssi_info_str}")
                    rx_logger.log_rx_event(event_type="DATA_FRAME_RECV_FULL", frame_seq_recv=frame_seq, data_len_byte_value=content_len, rssi_dbm=rssi_dbm)

                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"  수신된 페이로드 데이터:\n  {bytes_to_hex_pretty_str(payload_chunk)}")

                    # 데이터 수신 ACK 전송
                    _send_control_response(ser, frame_seq, ACK_TYPE_DATA)

                    try:
                        # 디코더 호출
                        payload_dict = decode_frame_payload(payload_chunk)

                        if payload_dict:
                            received_message_count += 1
                            logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{frame_seq:02x}) 디코딩 성공 ---")
                            
                            # 데이터 처리 및 로깅
                            ts_val = payload_dict.get('ts', 0.0)
                            latency_ms = int((time.time() - ts_val) * 1000) if ts_val > 0 else 0
                            
                            rx_logger.log_rx_event(event_type="DECODE_SUCCESS", frame_seq_recv=frame_seq, decoded_latency_ms=latency_ms)
                            
                            accel = payload_dict.get('accel', {})
                            gyro = payload_dict.get('gyro', {})
                            angle = payload_dict.get('angle', {})
                            gps = payload_dict.get('gps', {})
                            
                            # ... (콘솔 출력 로직) ...
                            log_lines = [
                                f"  Timestamp: {datetime.datetime.fromtimestamp(ts_val).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (Latency: {latency_ms}ms)",
                                f"  Accel(g): Ax={accel.get('ax', 0):.3f}, Ay={accel.get('ay', 0):.3f}, Az={accel.get('az', 0):.3f}",
                                f"  Gyro(°/s): Gx={gyro.get('gx', 0):.1f}, Gy={gyro.get('gy', 0):.1f}, Gz={gyro.get('gz', 0):.1f}",
                                f"  Angle(°): Roll={angle.get('roll', 0):.1f}, Pitch={angle.get('pitch', 0):.1f}, Yaw={angle.get('yaw', 0):.1f}",
                                f"  GPS: Lat={gps.get('lat', 0):.6f}, Lon={gps.get('lon', 0):.6f}, Alt={gps.get('altitude', 0):.1f}m",
                                f"  RSSI: {rssi_dbm} dBm" if rssi_dbm is not None else "  RSSI: N/A"
                            ]
                            for line in log_lines: logger.info(line)
                            
                            meta = {"recv_frame_seq": frame_seq, "latency_ms": latency_ms, "rssi_dbm": rssi_dbm}
                            _log_json(payload_dict, meta)
                            logger.info(f"  [OK#{received_message_count} FRAME_SEQ:0x{frame_seq:02x}] JSON 저장 완료.")

                        else: # 디코딩 실패
                            logger.error(f"메시지 (FRAME_SEQ: 0x{frame_seq:02x}): 디코딩 실패. PAYLOAD_LEN={len(payload_chunk)}B")
                            rx_logger.log_rx_event(event_type="DECODE_FAIL", frame_seq_recv=frame_seq, notes="decode_frame_payload returned None")

                    except Exception as e_decode:
                        logger.error(f"메시지 처리 중 오류 (FRAME_SEQ: 0x{frame_seq:02x}): {e_decode}", exc_info=True)
                        rx_logger.log_rx_event(event_type="DECODE_PROCESS_ERROR", frame_seq_recv=frame_seq, notes=str(e_decode))
                
                else: # 데이터 프레임 불완전 수신
                    logger.warning(f"데이터 프레임 내용 수신 실패: 기대 {content_len}B, 수신 {len(content_bytes)}B.")
                    rx_logger.log_rx_event(event_type="DATA_FRAME_CONTENT_FAIL", data_len_byte_value=content_len)
                continue

            # else: # 알 수 없는 첫 바이트는 무시
            #     pass

    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
        rx_logger.log_rx_event(event_type="KEYBOARD_INTERRUPT")
        # --- PDR 계산 및 출력 ---
        logger.info(f"--- PDR (Packet Delivery Rate) ---")
        if expected_total_packets_for_pdr > 0:
            pdr = (received_message_count / expected_total_packets_for_pdr) * 100
            logger.info(f"  기대 총 패킷 수: {expected_total_packets_for_pdr}")
            logger.info(f"  성공적으로 수신/디코딩된 패킷 수: {received_message_count}")
            logger.info(f"  PDR: {pdr:.2f}%")
            rx_logger.log_rx_event(event_type="PDR_CALCULATED", expected_packets=expected_total_packets_for_pdr, received_packets=received_message_count, pdr_percentage=float(f"{pdr:.2f}"))
        else:
            logger.warning(f"  기대 총 패킷 수가 0이므로 PDR을 계산할 수 없습니다.")
            logger.info(f"  성공적으로 수신/디코딩된 패킷 수: {received_message_count}")
    
    except Exception as e_global:
        logger.error(f"전역 예외 발생: {e_global}", exc_info=True)
        rx_logger.log_rx_event(event_type="GLOBAL_EXCEPTION", notes=str(e_global))
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")
            rx_logger.log_rx_event(event_type="SERIAL_PORT_CLOSED")

if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    receive_loop()