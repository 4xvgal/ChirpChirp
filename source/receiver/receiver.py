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
from typing import List # List 임포트 추가

try:
    import decoder
except ImportError as e_import:
    print(f"모듈 임포트 실패: {e_import}. decoder.py가 같은 폴더에 있거나 PYTHONPATH에 설정되어 있는지 확인하세요.")
    # import sys; sys.exit(1)

# ────────── 설정 (이전과 동일) ──────────
PORT         = os.getenv("LORA_PORT", "/dev/ttyAMA0")
BAUD         = 9600
SERIAL_READ_TIMEOUT = 0.05
INITIAL_SYN_TIMEOUT = 65.0 # 핸드셰이크 SYN 대기 시간
SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55
ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00
MIN_FRAME_CONTENT_LEN = 1
MAX_FRAME_CONTENT_LEN = 1 + 56 # encoder.MAX_PAYLOAD_CHUNK
KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST]
VALID_DATA_PKT_LENGTH_RANGE = range(MIN_FRAME_CONTENT_LEN, MAX_FRAME_CONTENT_LEN + 1)
DATA_DIR     = "data/raw_received"
os.makedirs(DATA_DIR, exist_ok=True)

# 로깅 설정 (시간 포맷을 이전 요청 로그와 유사하게 맞춤)
logging.basicConfig(
    level=logging.INFO, # 기본 로깅 레벨
    format="%(asctime)s - %(levelname)s - %(message)s", # 이전 로그 형식과 동일하게
    datefmt="%H:%M:%S" # 이전 로그 형식과 동일하게
)
logger = logging.getLogger(__name__)
# --------------------------------------

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
    if not data_bytes: return "<empty>"
    hex_str = binascii.hexlify(data_bytes).decode('ascii')
    lines: List[str] = []
    for i in range(0, len(hex_str), bytes_per_line * 2):
        chunk = hex_str[i:i + bytes_per_line * 2]
        # 이전 로그에는 오프셋 없었으므로 제거, 바이트 간 공백만 유지
        spaced_chunk = ' '.join(chunk[j:j+2] for j in range(0, len(chunk), 2))
        lines.append(spaced_chunk)
    return "\n  ".join(lines) if lines else "<empty>" # 이전 로그는 한 줄로 나왔으므로 \n 제거 가능성
    # 이전 로그의 DEBUG 데이터는 한 줄로 나왔으므로, 아래와 같이 변경 가능
    # return ' '.join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))


def _log_json(payload: dict, meta: dict): # JSON 로깅은 내부적으로 유지
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + "_received.jsonl"
    log_file_path = os.path.join(DATA_DIR, fn)
    try:
        with open(log_file_path, "a", encoding="utf-8") as fp:
            log_entry = {
                "rx_ts_utc": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
                "decoded_payload": payload,
                "rx_meta": meta
            }
            fp.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except IOError as e:
        logger.error(f"JSON 로그 파일 쓰기 실패 ({log_file_path}): {e}")

def _send_control_response(s: serial.Serial, seq: int, ack_type: int) -> bool:
    ack_bytes = struct.pack("!BB", ack_type, seq)
    try:
        written = s.write(ack_bytes)
        s.flush()
        type_name = {ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK", ACK_TYPE_DATA: "DATA_ACK", ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"}.get(ack_type, f"UNKNOWN_ACK_TYPE_0x{ack_type:02x}")
        # 이전 로그 형식에 맞게 메시지 수정
        logger.info(f"CTRL RSP TX: TYPE={type_name} (0x{ack_type:02x}), SEQ={seq if ack_type != ACK_TYPE_HANDSHAKE else 0:#0x})") # 핸드셰이크 SEQ는 0x0으로 표시
        if logger.isEnabledFor(logging.DEBUG):
             # 이전 로그의 DEBUG 데이터는 한 줄로 나왔음
             logger.debug(f"  데이터: {' '.join(f'{b:02x}' for b in ack_bytes)}")
        return written == len(ack_bytes)
    except serial.SerialException as e: logger.error(f"CTRL RSP TX 시리얼 오류 (TYPE=0x{ack_type:02x}, SEQ={seq}): {e}"); return False
    except Exception as e: logger.error(f"CTRL RSP TX 중 일반 오류 (TYPE=0x{ack_type:02x}, SEQ={seq}): {e}"); return False

def receive_loop():
    ser = None
    try:
        # logger.info(f"시리얼 포트 {PORT} (Baud: {BAUD}) 연결 시도...") # 이전 로그에 없음
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = None
        # logger.info(f"시리얼 포트 {PORT} 연결 성공.") # 이전 로그에 없음
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 {PORT} 열기 실패: {e}"); return

    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout}s)...") # 소수점 제거
        try:
            line = ser.readline()
            if line == SYN_MSG:
                # 이전 로그 형식에 맞게 SEQ=0x0으로 표시
                logger.info(f"SYN 수신, 핸드셰이크 ACK (TYPE={ACK_TYPE_HANDSHAKE:#0x}, SEQ={HANDSHAKE_ACK_SEQ:#0x}) 전송")
                if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                    handshake_success = True; logger.info("핸드셰이크 완료. 데이터 수신 대기 중..."); break
                else: logger.error("핸드셰이크 ACK 전송 실패. 1초 후 재시도..."); time.sleep(1)
            elif not line: logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
            else: logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}. 입력 버퍼 초기화 시도."); ser.reset_input_buffer(); time.sleep(0.1) # 이전 로그에는 hex 출력 없음
        except serial.SerialException as e_hs_serial: logger.error(f"핸드셰이크 중 시리얼 오류: {e_hs_serial}. 5초 후 재시도..."); time.sleep(5)
        except Exception as e_hs: logger.error(f"핸드셰이크 중 예기치 않은 오류: {e_hs}. 1초 후 재시도..."); time.sleep(1)

    if not handshake_success: logger.critical("핸드셰이크 최종 실패. 프로그램 종료."); ser.close(); return

    ser.timeout = SERIAL_READ_TIMEOUT
    ser.inter_byte_timeout = 0.1
    received_message_count = 0
    # logger.info(f"핸드셰이크 완료. 데이터 수신 대기 중 (Timeout: {ser.timeout:.2f}s, Inter-byte: {ser.inter_byte_timeout:.2f}s)...") # 이미 위에서 출력

    try:
        while True:
            first_byte_data = ser.read(1)
            if not first_byte_data: time.sleep(0.01); continue
            first_byte_val = first_byte_data[0]

            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER: # 제어 패킷
                packet_type = first_byte_val
                sequence_byte_data = ser.read(1)
                if sequence_byte_data:
                    sequence_num = sequence_byte_data[0]
                    # 이전 로그 형식에 맞게 SEQ=0xNN으로 표시
                    logger.info(f"제어 패킷 수신: TYPE=0x{packet_type:02x}, SEQ=0x{sequence_num:02x}")
                    if packet_type == QUERY_TYPE_SEND_REQUEST:
                        # logger.info(f"  전송 요청(QUERY) 수신 (SEQ={sequence_num}). 전송 허가(PERMIT) ACK 전송.") # 이전 로그에 없음
                        _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                    else: logger.warning(f"  알 수 없는/미처리 제어 패킷 타입: 0x{packet_type:02x}")
                else: logger.warning(f"제어 패킷의 시퀀스 번호 수신 실패 (TYPE=0x{packet_type:02x} 수신 후 타임아웃 또는 데이터 없음).")
            
            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE: # 데이터 패킷
                frame_content_len = first_byte_val
                payload_len_expected = frame_content_len - 1 if frame_content_len > 0 else 0
                logger.debug(f"데이터 패킷 LEN 바이트 수신: {frame_content_len}") # 이전 로그 형식과 동일

                frame_content_bytes_list = []
                bytes_remaining = frame_content_len
                read_successful = True
                while bytes_remaining > 0:
                    chunk = ser.read(bytes_remaining)
                    if not chunk: logger.warning(f"FRAME_CONTENT 수신 중 타임아웃: 기대 {frame_content_len}B 중 {frame_content_len - bytes_remaining}B 수신 후 타임아웃. 나머지 {bytes_remaining}B."); read_successful = False; break
                    frame_content_bytes_list.append(chunk); bytes_remaining -= len(chunk)
                
                if not read_successful:
                    logger.warning(f"  불완전한 FRAME_CONTENT 수신. 데이터 폐기.")
                    if ser.in_waiting > 0: junk = ser.read(ser.in_waiting); logger.debug(f"  불완전 수신 후 추가로 버려진 데이터 ({len(junk)}B): {' '.join(f'{b:02x}' for b in junk)}")
                    continue

                frame_content_bytes = b"".join(frame_content_bytes_list)
                message_seq_received = frame_content_bytes[0]
                payload_chunk_received = frame_content_bytes[1:]

                # 이전 로그 형식과 동일
                logger.info(f"데이터 프레임 수신: LEN={frame_content_len}, FRAME_SEQ={message_seq_received}, PAYLOAD_LEN={len(payload_chunk_received)}")
                _send_control_response(ser, message_seq_received, ACK_TYPE_DATA)

                try:
                    payload_dict = decoder.decompress_data(payload_chunk_received)
                    if payload_dict is None:
                        logger.error(f"메시지 (MESSAGE_SEQ: {message_seq_received}): 디코딩 실패. PAYLOAD_CHUNK_LEN={len(payload_chunk_received)}"); continue
                    
                    received_message_count += 1
                    # 이전 로그 형식과 동일하게 메시지 헤더 출력
                    logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: {message_seq_received}) 수신 데이터 (payload) ---")
                    
                    ts_value = payload_dict.get('ts', 0.0) 
                    try: 
                        ts_dt = datetime.datetime.fromtimestamp(float(ts_value))
                        # 이전 로그는 년월일 시간만 표시
                        ts_human_readable = ts_dt.strftime('%Y-%m-%d %H:%M:%S') # 밀리초 제거
                        # 이전 로그는 .000 붙임
                        ts_value_display = f"{float(ts_value):.3f}" if isinstance(ts_value, (int, float)) else str(ts_value)
                        ts_human_readable_final = f"{ts_human_readable}.{ts_value_display.split('.')[1] if '.' in ts_value_display else '000'}"

                    except (ValueError, TypeError): 
                        ts_human_readable_final = "N/A (Invalid Timestamp)"
                        ts_value_display = str(ts_value)


                    accel = payload_dict.get('accel', {})
                    gyro = payload_dict.get('gyro', {})
                    angle = payload_dict.get('angle', {})
                    gps = payload_dict.get('gps', {})

                    def format_value(val, fmt_str, default_val="N/A"): # 딕셔너리 직접 안받음
                        if val is None: return default_val
                        try: return format(float(val), fmt_str) 
                        except (ValueError, TypeError): return str(val)

                    # 이전 로그 형식에 맞춰 출력
                    logger.info(f"  Timestamp: {ts_human_readable_final} (raw: {ts_value_display})")
                    logger.info(f"  Accel (g): Ax={format_value(accel.get('ax'), '.3f')}, Ay={format_value(accel.get('ay'), '.3f')}, Az={format_value(accel.get('az'), '.3f')}")
                    logger.info(f"  Gyro (°/s): Gx={format_value(gyro.get('gx'), '.1f')}, Gy={format_value(gyro.get('gy'), '.1f')}, Gz={format_value(gyro.get('gz'), '.1f')}")
                    logger.info(f"  Angle (°): Roll={format_value(angle.get('roll'), '.1f')}, Pitch={format_value(angle.get('pitch'), '.1f')}, Yaw={format_value(angle.get('yaw'), '.1f')}")
                    logger.info(f"  GPS (°): Lat={format_value(gps.get('lat'), '.6f')}, Lon={format_value(gps.get('lon'), '.6f')}")
                    
                    # === 요청하신 고도 데이터 추가 부분 ===
                    # decoder.py에서 'altitude'를 제공하므로 이 값을 사용
                    logger.info(f"  GPS Altitude (m): {format_value(gps.get('altitude'), '.1f')}")
                    # ====================================
                    
                    # 이전 로그 형식의 Latency 출력
                    current_time_for_meta = time.time()
                    latency_val_ms = 0
                    if isinstance(ts_value, (int, float)) and ts_value > 0:
                        try: latency_val_ms = int((current_time_for_meta - float(ts_value)) * 1000)
                        except (ValueError, TypeError): pass
                    
                    logger.info(f"  [OK#{received_message_count} FRAME_SEQ:{message_seq_received}] Latency (sensor): {latency_val_ms}ms")
                    
                    # JSON 로깅은 내부적으로 계속 수행 (콘솔 출력과 별개)
                    meta_data = {
                        "message_seq_rx": message_seq_received,
                        "bytes_payload_chunk_rx": len(payload_chunk_received),
                        "latency_ms_e2e_approx": latency_val_ms,
                        "total_bytes_on_wire_per_msg": 1 + frame_content_len
                    }
                    _log_json(payload_dict, meta_data) # payload_dict는 이미 고도 포함

                except Exception as e_decode_process:
                    logger.error(f"메시지 처리(디코딩/출력 등) 중 오류 (MESSAGE_SEQ: {message_seq_received}): {e_decode_process}", exc_info=True)
            
            else: # 알 수 없는 첫 바이트
                logger.warning(f"알 수 없는 첫 바이트 또는 데이터 길이 범위 벗어남: 0x{first_byte_val:02x}. 입력 버퍼 내용 확인 및 비우기 시도.")
                if ser.in_waiting > 0: junk = ser.read(ser.in_waiting); logger.debug(f"  알 수 없는 바이트 (0x{first_byte_val:02x}) 수신 후 버려진 추가 데이터 ({len(junk)}B): {' '.join(f'{b:02x}' for b in junk)}")
                else: logger.debug(f"  알 수 없는 바이트 (0x{first_byte_val:02x}) 수신 후 버퍼에 추가 데이터 없음.")
                time.sleep(0.1)
            
    except KeyboardInterrupt: logger.info("수신 중단 요청 (KeyboardInterrupt). 프로그램 종료 중...")
    except serial.SerialException as e_global_serial: logger.error(f"전역 시리얼 예외 발생: {e_global_serial}. 프로그램 종료.", exc_info=True)
    except Exception as e_global: logger.error(f"전역 예외 발생: {e_global}. 프로그램 종료.", exc_info=True)
    finally:
        if ser and ser.is_open: ser.close(); logger.info(f"시리얼 포트 {PORT} 닫힘.")
        logger.info("수신 루프 종료.")

if __name__ == "__main__":
    # logger.info("수신기 애플리케이션 시작...") # 이전 로그에 없음
    # logger.setLevel(logging.DEBUG) # 이전 로그는 DEBUG 레벨 사용
    logging.getLogger().setLevel(logging.DEBUG) # 모든 로거를 DEBUG로 (이전 로그와 동일하게)
    # logging.getLogger('decoder').setLevel(logging.DEBUG)
    receive_loop()
    # logger.info("수신기 애플리케이션 종료.") # 이전 로그에 없음