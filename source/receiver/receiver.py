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
INITIAL_SYN_TIMEOUT = 65.0
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d - %(levelname)s - [%(module)s.%(funcName)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
# --------------------------------------

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
    if not data_bytes: return "<empty>"
    hex_str = binascii.hexlify(data_bytes).decode('ascii')
    lines: List[str] = []
    for i in range(0, len(hex_str), bytes_per_line * 2):
        chunk = hex_str[i:i + bytes_per_line * 2]
        spaced_chunk = ' '.join(chunk[j:j+2] for j in range(0, len(chunk), 2))
        lines.append(f"{i//(bytes_per_line*2):04x}: {spaced_chunk}")
    return "\n  ".join(lines) if lines else "<empty>"

def _log_json(payload: dict, meta: dict):
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
        logger.info(f"CTRL RSP TX: TYPE={type_name} (0x{ack_type:02x}), SEQ={seq}")
        if logger.isEnabledFor(logging.DEBUG): logger.debug(f"  TX Bytes: {bytes_to_hex_pretty_str(ack_bytes)}")
        return written == len(ack_bytes)
    except serial.SerialException as e: logger.error(f"CTRL RSP TX 시리얼 오류 (TYPE=0x{ack_type:02x}, SEQ={seq}): {e}"); return False
    except Exception as e: logger.error(f"CTRL RSP TX 중 일반 오류 (TYPE=0x{ack_type:02x}, SEQ={seq}): {e}"); return False

def receive_loop():
    ser = None
    try:
        logger.info(f"시리얼 포트 {PORT} (Baud: {BAUD}) 연결 시도...")
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = None
        logger.info(f"시리얼 포트 {PORT} 연결 성공.")
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 {PORT} 열기 실패: {e}"); return

    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout:.2f}s)...")
        try:
            line = ser.readline()
            if line == SYN_MSG:
                logger.info(f"SYN 수신. 핸드셰이크 ACK (TYPE={ACK_TYPE_HANDSHAKE:#04x}, SEQ={HANDSHAKE_ACK_SEQ:#04x}) 전송 시도.")
                if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                    handshake_success = True; logger.info("핸드셰이크 성공!"); break
                else: logger.error("핸드셰이크 ACK 전송 실패. 1초 후 재시도..."); time.sleep(1)
            elif not line: logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
            else: logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r} ({bytes_to_hex_pretty_str(line)}). 입력 버퍼 초기화 시도."); ser.reset_input_buffer(); time.sleep(0.1)
        except serial.SerialException as e_hs_serial: logger.error(f"핸드셰이크 중 시리얼 오류: {e_hs_serial}. 5초 후 재시도..."); time.sleep(5)
        except Exception as e_hs: logger.error(f"핸드셰이크 중 예기치 않은 오류: {e_hs}. 1초 후 재시도..."); time.sleep(1)

    if not handshake_success: logger.critical("핸드셰이크 최종 실패. 프로그램 종료."); ser.close(); return

    ser.timeout = SERIAL_READ_TIMEOUT
    ser.inter_byte_timeout = 0.1
    received_message_count = 0
    logger.info(f"핸드셰이크 완료. 데이터 수신 대기 중 (Timeout: {ser.timeout:.2f}s, Inter-byte: {ser.inter_byte_timeout:.2f}s)...")

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
                    logger.info(f"제어 패킷 수신: TYPE=0x{packet_type:02x}, SEQ=0x{sequence_num:02x}")
                    if packet_type == QUERY_TYPE_SEND_REQUEST:
                        logger.info(f"  전송 요청(QUERY) 수신 (SEQ={sequence_num}). 전송 허가(PERMIT) ACK 전송.")
                        _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                    else: logger.warning(f"  알 수 없는/미처리 제어 패킷 타입: 0x{packet_type:02x}")
                else: logger.warning(f"제어 패킷의 시퀀스 번호 수신 실패 (TYPE=0x{packet_type:02x} 수신 후 타임아웃 또는 데이터 없음).")
            
            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE: # 데이터 패킷
                frame_content_len = first_byte_val
                payload_len_expected = frame_content_len - 1 if frame_content_len > 0 else 0
                logger.debug(f"데이터 패킷 길이 바이트 수신: {frame_content_len} (내부 페이로드 길이: {payload_len_expected})")

                frame_content_bytes_list = []
                bytes_remaining = frame_content_len
                read_successful = True
                while bytes_remaining > 0:
                    chunk = ser.read(bytes_remaining)
                    if not chunk: logger.warning(f"FRAME_CONTENT 수신 중 타임아웃: 기대 {frame_content_len}B 중 {frame_content_len - bytes_remaining}B 수신 후 타임아웃. 나머지 {bytes_remaining}B."); read_successful = False; break
                    frame_content_bytes_list.append(chunk); bytes_remaining -= len(chunk)
                
                if not read_successful:
                    logger.warning(f"  불완전한 FRAME_CONTENT 수신. 데이터 폐기.")
                    if ser.in_waiting > 0: junk = ser.read(ser.in_waiting); logger.debug(f"  불완전 수신 후 추가로 버려진 데이터 ({len(junk)}B):\n  {bytes_to_hex_pretty_str(junk)}")
                    continue

                frame_content_bytes = b"".join(frame_content_bytes_list)
                message_seq_received = frame_content_bytes[0]
                payload_chunk_received = frame_content_bytes[1:]

                logger.info(f"데이터 프레임 온전히 수신: 전체길이(LENGTH_BYTE 값)={frame_content_len}, MESSAGE_SEQ={message_seq_received}, PAYLOAD_CHUNK_LEN={len(payload_chunk_received)}")
                _send_control_response(ser, message_seq_received, ACK_TYPE_DATA)

                try:
                    payload_dict = decoder.decompress_data(payload_chunk_received)
                    if payload_dict is None:
                        logger.error(f"메시지 (MESSAGE_SEQ: {message_seq_received}): 디코딩 실패. PAYLOAD_CHUNK_LEN={len(payload_chunk_received)}"); continue
                    
                    received_message_count += 1
                    logger.info(f"--- 메시지 #{received_message_count} (MESSAGE_SEQ: {message_seq_received}) 수신 데이터 ---")
                    
                    ts_value = payload_dict.get('ts', 0.0) 
                    try: ts_dt = datetime.datetime.fromtimestamp(float(ts_value)); ts_human_readable = ts_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] 
                    except (ValueError, TypeError): ts_human_readable = "N/A (Invalid Timestamp)"; ts_value_display = str(ts_value)
                    else: ts_value_display = f"{ts_value:.3f}"

                    accel = payload_dict.get('accel', {})
                    gyro = payload_dict.get('gyro', {})
                    angle = payload_dict.get('angle', {})
                    gps = payload_dict.get('gps', {}) # decoder.py가 lat, lon, altitude 제공

                    def format_value(data_dict, key, fmt_str, default_val="N/A"):
                        val = data_dict.get(key)
                        if val is None: return default_val
                        try: return format(float(val), fmt_str) 
                        except (ValueError, TypeError): return str(val)

                    display_lines = [
                        f"  Timestamp      : {ts_human_readable} (raw: {ts_value_display})",
                        f"  Accelerometer (g): Ax={format_value(accel, 'ax', '.4f')}, Ay={format_value(accel, 'ay', '.4f')}, Az={format_value(accel, 'az', '.4f')}",
                        f"  Gyroscope (°/s): Gx={format_value(gyro, 'gx', '.2f')}, Gy={format_value(gyro, 'gy', '.2f')}, Gz={format_value(gyro, 'gz', '.2f')}",
                        f"  Angle (°):       Roll={format_value(angle, 'roll', '.2f')}, Pitch={format_value(angle, 'pitch', '.2f')}, Yaw={format_value(angle, 'yaw', '.2f')}",
                        f"  GPS:             Lat={format_value(gps, 'lat', '.6f')}, Lon={format_value(gps, 'lon', '.6f')}",
                        # 고도(altitude)는 이제 decoder에서 제공됨
                        f"                   Altitude={format_value(gps, 'altitude', '.1f')}m", # 단위 'm' 추가
                        # 여전히 인코딩되지 않는 GPS 필드 (필요하다면 encoder/decoder에 추가)
                        f"                   Satellites=N/A (not encoded)",
                        f"                   Fix Quality=N/A (not encoded)"
                    ]
                    
                    for line in display_lines: logger.info(line)
                    logger.info("-" * 40)

                    current_time_for_meta = time.time()
                    latency_val = 0
                    if isinstance(ts_value, (int, float)) and ts_value > 0:
                        try: latency_val = int((current_time_for_meta - float(ts_value)) * 1000)
                        except (ValueError, TypeError): pass

                    meta_data = {
                        "message_seq_rx": message_seq_received,
                        "bytes_payload_chunk_rx": len(payload_chunk_received),
                        "latency_ms_e2e_approx": latency_val,
                        "total_bytes_on_wire_per_msg": 1 + frame_content_len
                    }
                    logger.info(f"  [OK#{received_message_count} MSG_SEQ:{message_seq_received}] Latency (approx): {meta_data['latency_ms_e2e_approx']}ms, Payload chunk: {meta_data['bytes_payload_chunk_rx']}B")
                    _log_json(payload_dict, meta_data) # payload_dict는 이미 고도 포함

                except Exception as e_decode_process:
                    logger.error(f"메시지 처리(디코딩/출력 등) 중 오류 (MESSAGE_SEQ: {message_seq_received}): {e_decode_process}", exc_info=True)
            
            else: # 알 수 없는 첫 바이트
                logger.warning(f"알 수 없는 첫 바이트 또는 데이터 길이 범위 벗어남: 0x{first_byte_val:02x}. 입력 버퍼 내용 확인 및 비우기 시도.")
                if ser.in_waiting > 0: junk = ser.read(ser.in_waiting); logger.debug(f"  알 수 없는 바이트 (0x{first_byte_val:02x}) 수신 후 버려진 추가 데이터 ({len(junk)}B):\n  {bytes_to_hex_pretty_str(junk)}")
                else: logger.debug(f"  알 수 없는 바이트 (0x{first_byte_val:02x}) 수신 후 버퍼에 추가 데이터 없음.")
                time.sleep(0.1)
            
    except KeyboardInterrupt: logger.info("수신 중단 요청 (KeyboardInterrupt). 프로그램 종료 중...")
    except serial.SerialException as e_global_serial: logger.error(f"전역 시리얼 예외 발생: {e_global_serial}. 프로그램 종료.", exc_info=True)
    except Exception as e_global: logger.error(f"전역 예외 발생: {e_global}. 프로그램 종료.", exc_info=True)
    finally:
        if ser and ser.is_open: ser.close(); logger.info(f"시리얼 포트 {PORT} 닫힘.")
        logger.info("수신 루프 종료.")

if __name__ == "__main__":
    logger.info("수신기 애플리케이션 시작...")
    # logger.setLevel(logging.DEBUG) # 디버깅 시 상세 로그
    # logging.getLogger('decoder').setLevel(logging.DEBUG) # 디코더 상세 로그
    receive_loop()
    logger.info("수신기 애플리케이션 종료.")