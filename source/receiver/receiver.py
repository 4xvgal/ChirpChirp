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
import binascii # 로깅용

try:
    import decoder # 같은 폴더에 있다고 가정
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. decoder.py가 같은 폴더에 있는지 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0" # 실제 환경에 맞게 수정
BAUD         = 9600

# 타임아웃
SERIAL_READ_TIMEOUT = 0.05   # 일반적인 non-blocking read 시도 시 짧은 타임아웃
CONTROL_PKT_WAIT_TIMEOUT = 65.0 # Query 등에 대한 응답을 기다릴 때의 타임아웃 (sender와 유사)
INITIAL_SYN_TIMEOUT = 65.0 # 최초 SYN 메시지 대기 시간

# 메시지 타입 (sender와 동일하게)
SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55

# 패킷 구조 관련 (sender와 동일하게)
ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00

# 새 프레임 구조: LEN(1B) + SEQ(1B) + PAYLOAD_CHUNK(가변)
NEW_FRAME_MAX_CONTENT_LEN = 1 + 56  # SEQ(1) + PAYLOAD_CHUNK(최대 56)
NEW_MIN_FRAME_CONTENT_LEN = 1 + 0   # SEQ(1) + PAYLOAD_CHUNK(최소 0)

DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str: # sender.py에서 가져옴
    if not data_bytes:
        return "<empty>"
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
            "ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def _send_control_response(s: serial.Serial, seq: int, ack_type: int) -> bool:
    ack_bytes = struct.pack("!BB", seq, ack_type)
    try:
        written = s.write(ack_bytes)
        s.flush()
        type_name = {
            ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK",
            ACK_TYPE_DATA: "DATA_ACK",
            ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"
        }.get(ack_type, f"UNKNOWN_TYPE_0x{ack_type:02x}")
        logger.info(f"CTRL RSP TX: SEQ={seq}, TYPE={type_name} (0x{ack_type:02x})")
        if logger.isEnabledFor(logging.DEBUG):
             logger.debug(f"  데이터: {bytes_to_hex_pretty_str(ack_bytes)}")
        return written == len(ack_bytes)
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (SEQ={seq}, TYPE=0x{ack_type:02x}): {e}")
        return False

def receive_loop():
    ser = None
    try:
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT) # 초기 SYN 대기 타임아웃
        ser.inter_byte_timeout = None # 명시적 None
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패: {e}")
        return

    # --- 핸드셰이크 ---
    handshake_success = False
    while not handshake_success: # 핸드셰이크는 성공할 때까지 (또는 외부 중단까지)
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout}s)...")
        try:
            line = ser.readline() # readline은 \n을 만나거나 타임아웃 시 반환
            if line == SYN_MSG:
                logger.info(f"SYN 수신, 핸드셰이크 ACK (SEQ={HANDSHAKE_ACK_SEQ}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 전송")
                if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                    handshake_success = True
                    break
                else:
                    logger.error("핸드셰이크 ACK 전송 실패. 1초 후 재시도...")
                    time.sleep(1) # ACK 전송 실패 시 짧은 대기
            elif not line: # 타임아웃
                logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
                # ser.timeout은 그대로 유지, 루프 재시작
            else: # 예상치 못한 데이터
                logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}. 입력 버퍼 초기화 시도.")
                ser.reset_input_buffer() # 버퍼 비우기
                time.sleep(0.1)
        except Exception as e_hs:
            logger.error(f"핸드셰이크 중 오류: {e_hs}. 1초 후 재시도...")
            time.sleep(1)


    if not handshake_success: # KeyboardInterrupt 등으로 루프 탈출 시
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        if ser and ser.is_open:
            ser.close()
        return

    # 핸드셰이크 성공 후, 일반 수신 모드 타임아웃 설정
    ser.timeout = SERIAL_READ_TIMEOUT # 짧은 타임아웃으로 변경하여 폴링 유사 효과
    ser.inter_byte_timeout = 0.1    # 프레임 내 바이트간 간격

    received_message_count = 0
    logger.info("핸드셰이크 완료. 데이터 수신 대기 중...")

    try:
        while True:
            # 1. 데이터 패킷의 LEN 바이트를 먼저 시도 (짧은 타임아웃)
            len_byte = ser.read(1)

            if len_byte: # 데이터 패킷의 시작 (LEN 바이트 수신)
                actual_content_len = len_byte[0]
                logger.debug(f"LEN 바이트 수신: {actual_content_len}")

                if not (NEW_MIN_FRAME_CONTENT_LEN <= actual_content_len <= NEW_FRAME_MAX_CONTENT_LEN):
                    logger.warning(f"잘못된 LEN 값 수신: {actual_content_len} (기대 범위: {NEW_MIN_FRAME_CONTENT_LEN}-{NEW_FRAME_MAX_CONTENT_LEN}). 입력 버퍼 초기화.")
                    if ser.in_waiting > 0:
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  잘못된 LEN 후 버려진 데이터: {bytes_to_hex_pretty_str(junk)}")
                    continue

                # 실제 내용 (SEQ + PAYLOAD_CHUNK) 읽기
                # inter_byte_timeout이 여기서 중요하게 작용
                actual_content_bytes = ser.read(actual_content_len)
                
                if len(actual_content_bytes) != actual_content_len:
                    logger.warning(f"프레임 내용 수신 실패: 기대 {actual_content_len}B, 수신 {len(actual_content_bytes)}B. 데이터: {bytes_to_hex_pretty_str(actual_content_bytes)}")
                    if ser.in_waiting > 0: ser.read(ser.in_waiting) # 남은 데이터 비우기
                    continue

                actual_seq = actual_content_bytes[0]
                payload_chunk_from_actual_frame = actual_content_bytes[1:]

                logger.info(f"데이터 프레임 수신: LEN={actual_content_len}, SEQ={actual_seq}, PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}")

                # 데이터 ACK 전송
                _send_control_response(ser, actual_seq, ACK_TYPE_DATA)

                # 디코딩 및 데이터 처리
                try:
                    payload_dict = decoder.decompress_data(payload_chunk_from_actual_frame)
                    if payload_dict is None:
                        logger.error(f"메시지 (SEQ: {actual_seq}): 디코딩 실패 (결과 None).")
                        continue

                    received_message_count += 1
                    ts = payload_dict.get("ts", 0.0)
                    human_ts = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ts > 0 else "N/A"
                    # ... (나머지 데이터 출력 로직은 기존과 유사하게 구성) ...
                    logger.info(f"=== 메시지 #{received_message_count} (SEQ: {actual_seq}) 수신 완료 @ {human_ts} ===")
                    # logger.info(f"  Accel ... Gyro ... Angle ... GPS ...") # 상세 정보 출력

                    meta_data = {
                        "seq_num": actual_seq, # PKT_ID 대신 SEQ 사용
                        "bytes_compressed": len(payload_chunk_from_actual_frame),
                        "latency_ms_sensor": int((time.time() - ts) * 1000) if ts > 0 else 0,
                        "total_bytes_on_wire": 1 + actual_content_len
                    }
                    logger.info(f"  [OK#{received_message_count} SEQ:{actual_seq}] Latency (sensor): {meta_data['latency_ms_sensor']}ms")
                    _log_json(payload_dict, meta_data)

                except Exception as e_decode:
                    logger.error(f"메시지 처리(디코딩 등) 중 오류 (SEQ: {actual_seq}): {e_decode}")
                
            # 2. LEN 바이트가 없고 (타임아웃), 버퍼에 컨트롤 패킷 길이만큼 데이터가 있으면 컨트롤 패킷 시도
            elif ser.in_waiting >= ACK_PACKET_LEN:
                control_bytes = ser.read(ACK_PACKET_LEN) # PKT_ID 없으므로 2바이트
                try:
                    received_seq, received_type = struct.unpack("!BB", control_bytes)
                    logger.debug(f"컨트롤 패킷 수신 추정: SEQ={received_seq}, TYPE=0x{received_type:02x}")

                    if received_type == QUERY_TYPE_SEND_REQUEST:
                        logger.info(f"  송신 요청(Query) 수신: SEQ={received_seq}. Permit 전송.")
                        _send_control_response(ser, received_seq, ACK_TYPE_SEND_PERMIT)
                    else:
                        logger.warning(f"  알 수 없거나 예상치 않은 컨트롤 타입: 0x{received_type:02x}. 데이터: {bytes_to_hex_pretty_str(control_bytes)}")
                except struct.error:
                    logger.warning(f"  컨트롤 패킷 언패킹 실패: {bytes_to_hex_pretty_str(control_bytes)}")
                except Exception as e_ctrl_pkt:
                    logger.error(f"  컨트롤 패킷 처리 중 오류: {e_ctrl_pkt}")
            
            # 3. 아무 데이터도 수신되지 않음 (짧은 타임아웃)
            else:
                # logger.debug("유휴 상태 또는 데이터 대기 중...") # 너무 자주 로깅될 수 있음
                time.sleep(0.01) # CPU 사용 방지, 짧은 폴링 간격
                continue
            
    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
    except Exception as e_global:
        logger.error(f"전역 예외 발생: {e_global}", exc_info=True)
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")

if __name__ == "__main__":
    # 로그 레벨 조정 (필요시 DEBUG로)
    # logging.getLogger().setLevel(logging.DEBUG)
    # logging.getLogger('__main__').setLevel(logging.DEBUG) # 현재 모듈만 DEBUG
    receive_loop()