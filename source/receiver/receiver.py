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
    import decoder # 같은 폴더에 있다고 가정
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. decoder.py가 같은 폴더에 있는지 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0" # 실제 환경에 맞게 수정
BAUD         = 9600

SERIAL_READ_TIMEOUT = 0.05
CONTROL_PKT_WAIT_TIMEOUT = 65.0
INITIAL_SYN_TIMEOUT = 65.0

SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55

ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00

NEW_FRAME_MAX_CONTENT_LEN = 1 + 56
NEW_MIN_FRAME_CONTENT_LEN = 1 + 0

KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST]
VALID_DATA_PKT_LENGTH_RANGE = range(NEW_MIN_FRAME_CONTENT_LEN, NEW_FRAME_MAX_CONTENT_LEN + 1)


DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%H:%M:%S") # datefmt는 최상위 로거에만 적용됨. 상세 시간은 직접 포맷팅
logger = logging.getLogger(__name__)

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
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
    ack_bytes = struct.pack("!BB", ack_type, seq)
    try:
        written = s.write(ack_bytes)
        s.flush()
        type_name = {
            ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK",
            ACK_TYPE_DATA: "DATA_ACK",
            ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"
        }.get(ack_type, f"UNKNOWN_TYPE_0x{ack_type:02x}")
        logger.info(f"CTRL RSP TX: TYPE={type_name} (0x{ack_type:02x}), SEQ={seq}")
        if logger.isEnabledFor(logging.DEBUG):
             logger.debug(f"  데이터: {bytes_to_hex_pretty_str(ack_bytes)}")
        return written == len(ack_bytes)
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (TYPE=0x{ack_type:02x}, SEQ={seq}): {e}")
        return False

def receive_loop():
    ser = None
    try:
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = None
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패: {e}")
        return

    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout}s)...")
        try:
            line = ser.readline()
            if line == SYN_MSG:
                logger.info(f"SYN 수신, 핸드셰이크 ACK (TYPE={ACK_TYPE_HANDSHAKE:#02x}, SEQ={HANDSHAKE_ACK_SEQ:#02x}) 전송")
                if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                    handshake_success = True
                    break
                else:
                    logger.error("핸드셰이크 ACK 전송 실패. 1초 후 재시도...")
                    time.sleep(1)
            elif not line:
                logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
            else:
                logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}. 입력 버퍼 초기화 시도.")
                ser.reset_input_buffer()
                time.sleep(0.1)
        except Exception as e_hs:
            logger.error(f"핸드셰이크 중 오류: {e_hs}. 1초 후 재시도...")
            time.sleep(1)

    if not handshake_success:
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        if ser and ser.is_open:
            ser.close()
        return

    ser.timeout = SERIAL_READ_TIMEOUT
    ser.inter_byte_timeout = 0.1

    received_message_count = 0
    logger.info("핸드셰이크 완료. 데이터 수신 대기 중...")

    try:
        while True:
            first_byte_data = ser.read(1)

            if not first_byte_data:
                time.sleep(0.01)
                continue

            first_byte_val = first_byte_data[0]

            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                packet_type = first_byte_val
                if ser.in_waiting > 0:
                    sequence_byte_data = ser.read(1)
                    if sequence_byte_data:
                        sequence_num = sequence_byte_data[0]
                        logger.info(f"제어 패킷 수신: TYPE=0x{packet_type:02x}, SEQ=0x{sequence_num:02x}")
                        if packet_type == QUERY_TYPE_SEND_REQUEST:
                            _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                        else:
                            logger.warning(f"  예상치 못한 제어 패킷 타입 수신: 0x{packet_type:02x}")
                    else:
                        logger.warning(f"제어 패킷의 시퀀스 번호 수신 실패 (TYPE=0x{packet_type:02x}, 첫 바이트만 읽힘)")
                else:
                    logger.warning(f"제어 패킷의 시퀀스 번호 대기 중 데이터 없음 (TYPE=0x{packet_type:02x} 수신 후)")
            
            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                actual_content_len = first_byte_val
                logger.debug(f"데이터 패킷 LEN 바이트 수신: {actual_content_len}")

                actual_content_bytes = ser.read(actual_content_len)
                
                if len(actual_content_bytes) != actual_content_len:
                    logger.warning(f"프레임 내용 수신 실패: 기대 {actual_content_len}B, 수신 {len(actual_content_bytes)}B. 데이터: {bytes_to_hex_pretty_str(actual_content_bytes)}")
                    if ser.in_waiting > 0: ser.read(ser.in_waiting)
                    continue

                actual_seq = actual_content_bytes[0]
                payload_chunk_from_actual_frame = actual_content_bytes[1:]

                logger.info(f"데이터 프레임 수신: LEN={actual_content_len}, FRAME_SEQ={actual_seq}, PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}")
                _send_control_response(ser, actual_seq, ACK_TYPE_DATA)

                try:
                    payload_dict = decoder.decompress_data(payload_chunk_from_actual_frame)
                    if payload_dict is None:
                        logger.error(f"메시지 (FRAME_SEQ: {actual_seq}): 디코딩 실패 (결과 None). PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}")
                        continue

                    received_message_count += 1
                    
                    # --- 이전 코드의 데이터 상세 출력 로직 추가 ---
                    logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: {actual_seq}) 수신 데이터 (payload) ---") # 메시지 식별자 추가
                    
                    ts_value = payload_dict.get('ts', 0.0) 
                    # Unix 타임스탬프(float)를 datetime 객체로 변환 후 포맷팅
                    try:
                        # float으로 올바르게 변환되었는지 확인
                        ts_dt = datetime.datetime.fromtimestamp(float(ts_value))
                        ts_human_readable = ts_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    except (ValueError, TypeError):
                        ts_human_readable = "N/A (Invalid Timestamp)"
                        ts_value_display = str(ts_value) # 원본 값 그대로 표시
                    else:
                        ts_value_display = f"{ts_value:.3f}"


                    accel = payload_dict.get('accel', {})
                    gyro = payload_dict.get('gyro', {})
                    angle = payload_dict.get('angle', {})
                    gps = payload_dict.get('gps', {})

                    # 안전한 .get()과 소수점 포맷팅을 위한 헬퍼 함수 (선택 사항)
                    def format_value(data_dict, key, fmt_str, default_val="N/A"):
                        val = data_dict.get(key)
                        if val is None or not isinstance(val, (int, float)):
                            return default_val
                        try:
                            return format(float(val), fmt_str)
                        except (ValueError, TypeError):
                            return default_val

                    display_message = []
                    display_message.append(f"  Timestamp: {ts_human_readable} (raw: {ts_value_display})")
                    display_message.append(f"  Accel (g): Ax={format_value(accel, 'ax', '.3f')}, Ay={format_value(accel, 'ay', '.3f')}, Az={format_value(accel, 'az', '.3f')}")
                    display_message.append(f"  Gyro (°/s): Gx={format_value(gyro, 'gx', '.1f')}, Gy={format_value(gyro, 'gy', '.1f')}, Gz={format_value(gyro, 'gz', '.1f')}")
                    display_message.append(f"  Angle (°): Roll={format_value(angle, 'roll', '.1f')}, Pitch={format_value(angle, 'pitch', '.1f')}, Yaw={format_value(angle, 'yaw', '.1f')}")
                    display_message.append(f"  GPS (°): Lat={format_value(gps, 'lat', '.6f')}, Lon={format_value(gps, 'lon', '.6f')}")
                    
                    # 여러 줄 로그를 위해 logger.info를 여러 번 호출하거나, \n으로 합쳐서 한 번에 호출
                    for line in display_message:
                        logger.info(line)
                    # logger.info("\n".join(display_message)) # 또는 이렇게 한 번에
                    # --- 데이터 상세 출력 로직 끝 ---


                    # JSON 로깅을 위한 메타데이터 (기존 로직 유지)
                    current_time_for_meta = time.time() # meta 계산 시점 통일
                    meta_data = {
                        "seq_num": actual_seq,
                        "bytes_compressed": len(payload_chunk_from_actual_frame),
                        # ts_value가 유효한 float일 때만 latency 계산
                        "latency_ms_sensor": int((current_time_for_meta - float(ts_value)) * 1000) if isinstance(ts_value, (int,float)) and ts_value > 0 else 0,
                        "total_bytes_on_wire": 1 + actual_content_len
                    }
                    logger.info(f"  [OK#{received_message_count} FRAME_SEQ:{actual_seq}] Latency (sensor): {meta_data['latency_ms_sensor']}ms")
                    _log_json(payload_dict, meta_data)

                except Exception as e_decode:
                    logger.error(f"메시지 처리(디코딩/출력 등) 중 오류 (FRAME_SEQ: {actual_seq}): {e_decode}", exc_info=True) # exc_info 추가
            
            else:
                logger.warning(f"알 수 없는 첫 바이트 또는 잘못된 값 수신: 0x{first_byte_val:02x}. 입력 버퍼 내용 확인 및 비우기 시도.")
                if ser.in_waiting > 0:
                    junk = ser.read(ser.in_waiting)
                    logger.debug(f"  알 수 없는 바이트 (0x{first_byte_val:02x}) 후 버려진 데이터 ({len(junk)}B):\n  {bytes_to_hex_pretty_str(junk)}")
                else:
                    logger.debug(f"  알 수 없는 바이트 (0x{first_byte_val:02x}) 후 버퍼에 추가 데이터 없음.")
                time.sleep(0.1)
            
    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
    except Exception as e_global:
        logger.error(f"전역 예외 발생: {e_global}", exc_info=True)
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")

if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)
    receive_loop()