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
    import decoder # 같은 폴더에 있다고 가정 (decoder.py는 이전 단계에서 수정된 버전 사용)
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. decoder.py가 같은 폴더에 있는지 확인하세요.")
    exit(1)
# ────────── 로라 하드웨어 설정 ──────────
LORA_SERIAL_PORT   = "/dev/ttyAMA0"   # 실제 라즈베리파이 UART 디바이스
LORA_FREQ_MHZ      = 868            # MHz 단위 (건들 ㄴ)
LORA_ADDR          = 0xFFFF         # 현재 노드 주소 (0xFFFF = broadcast)
LORA_POWER_DBM     = 22             # 22 / 17 / 13 / 10
LORA_RSSI_ENABLE   = True           # 패킷 및 채널 RSSI 사용 여부
LORA_AIR_SPEED_BPS = 1200           # 1200~62500 중 지원값
LORA_NET_ID        = 0              # 네트워크 ID (0~255)
LORA_BUFFER_SIZE   = 240            # 240 / 128 / 64 / 32
LORA_CRYPT_KEY     = 0              # 0~65535 (0 = 암호화 비활성)
# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0"
BAUD         = 9600

SERIAL_READ_TIMEOUT = 0.05  # 기본 타임아웃 (데이터 프레임 내용, RSSI 바이트 등)
INITIAL_SYN_TIMEOUT = 65.0  # 핸드셰이크 SYN 대기 타임아웃

SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55

ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00
# ────────── 외부 모듈 import ──────────
try:
    from utils import sx126x  # LoRa 설정용 클래스
except ImportError as e:
    print(f"sx126x 모듈 임포트 실패: {e}. sx126x.py가 PYTHONPATH에 있는지 확인하세요.")
    exit(1)

# --- 로거 초기화 ---
logging.basicConfig(level=logging.INFO, # 기본 로깅 레벨 INFO
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
# logging.getLogger('decoder').setLevel(logging.DEBUG)
# --- 로거 초기화 끝 ---

# --- "알 수 없는 바이트" 필터링을 위한 리스트 ---
IGNORED_MODULE_BYTES = [
    0xEB, 0xEC, 0xED, 0xEE, 0xEF, 0xF0
]
if IGNORED_MODULE_BYTES:
    logger.info(f"다음 바이트 값들은 수신 시 무시됩니다 (모듈 자체 출력 추정): {[hex(b) for b in IGNORED_MODULE_BYTES]}")
# --- 필터링 리스트 정의 끝 ---

# --- VALID_DATA_PKT_LENGTH_RANGE 정의 ---
# 이 값은 LENGTH 바이트 자체의 값, 즉 (시퀀스 번호 1B + 압축된 페이로드 N B)의 길이. RSSI 바이트는 포함하지 않음.
MIN_COMPRESSED_PAYLOAD_LEN = 5
MAX_PAYLOAD_CHUNK_FROM_ENCODER = 56
NEW_MIN_FRAME_CONTENT_LEN = 1 + MIN_COMPRESSED_PAYLOAD_LEN # 시퀀스(1) + 최소페이로드
NEW_FRAME_MAX_CONTENT_LEN = 1 + MAX_PAYLOAD_CHUNK_FROM_ENCODER # 시퀀스(1) + 최대페이로드
VALID_DATA_PKT_LENGTH_RANGE = range(NEW_MIN_FRAME_CONTENT_LEN, NEW_FRAME_MAX_CONTENT_LEN + 1)
logger.info(f"수신 유효 데이터 프레임 콘텐츠 길이 범위(LENGTH 바이트 값): {list(VALID_DATA_PKT_LENGTH_RANGE)}")
# --- VALID_DATA_PKT_LENGTH_RANGE 정의 끝 ---

KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST]

DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

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
            "ts_recv_utc": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
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
        logger.info(f"CTRL RSP TX: TYPE={type_name} (0x{ack_type:02x}), SEQ=0x{seq:02x}")
        if logger.isEnabledFor(logging.DEBUG):
             logger.debug(f"  데이터: {bytes_to_hex_pretty_str(ack_bytes)}")
        return written == len(ack_bytes)
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (TYPE=0x{ack_type:02x}, SEQ=0x{seq:02x}): {e}")
        return False

def receive_loop():
    ser: Optional[serial.Serial] = None
    try:
        logger.info(f"시리얼 포트 {PORT} (Baud: {BAUD}) 열기 시도...")
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        # inter_byte_timeout: 바이트 간 최대 지연 시간.
        # 데이터 프레임의 LENGTH 바이트, Content, 그리고 RSSI 바이트가
        # 개별 read() 호출로 읽히므로, 각 read()는 ser.timeout의 영향을 받음.
        # 패킷의 바이트들이 연속적으로 도착한다면 inter_byte_timeout은 큰 의미가 없을 수 있지만,
        # 혹시 모를 바이트 간 지연을 처리하기 위해 짧게 설정할 수 있음.
        ser.inter_byte_timeout = 0.02 # 예: 20ms. None으로 두면 비활성화.
        logger.info(f"시리얼 포트 {PORT} 열기 성공. (timeout={ser.timeout}s, inter_byte_timeout={ser.inter_byte_timeout}s)")

    except serial.SerialException as e:
        logger.error(f"포트 열기 실패 ({PORT}): {e}")
        return

    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중 (Timeout: {ser.timeout}s)...")
        try:
            if ser.in_waiting > 0:
                ser.reset_input_buffer()
                logger.debug("핸드셰이크 시도 전 입력 버퍼 초기화됨.")
            line = ser.readline() # readline은 ser.timeout의 영향을 받음
            if line == SYN_MSG:
                logger.info(f"SYN 수신, 핸드셰이크 ACK (TYPE={ACK_TYPE_HANDSHAKE:#02x}, SEQ={HANDSHAKE_ACK_SEQ:#02x}) 전송")
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
                logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}.")
        except Exception as e_hs:
            logger.error(f"핸드셰이크 중 오류: {e_hs}. 1초 후 재시도...", exc_info=True)
            time.sleep(1)

    if not handshake_success:
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        if ser and ser.is_open:
            ser.close()
        return

    # 핸드셰이크 후 데이터 수신을 위한 타임아웃 설정
    ser.timeout = SERIAL_READ_TIMEOUT # 각 ser.read() 호출에 적용
    # ser.inter_byte_timeout = 0.02 # 위에서 설정했으므로 유지 또는 재설정
    logger.info(f"핸드셰이크 완료. 데이터 수신 대기 중... (timeout={ser.timeout}s, inter_byte_timeout={ser.inter_byte_timeout}s)")

    received_message_count = 0
    
    try:
        while True:
            # 1. 첫 바이트(타입 또는 길이) 읽기
            first_byte_data = ser.read(1) # ser.timeout(SERIAL_READ_TIMEOUT) 적용

            if not first_byte_data: # 타임아웃 발생
                continue

            first_byte_val = first_byte_data[0]

            if first_byte_val in IGNORED_MODULE_BYTES:
                logger.debug(f"모듈 자체 출력/노이즈 바이트 수신 및 무시: 0x{first_byte_val:02x}")
                if ser.in_waiting > 0:
                    time.sleep(0.01) 
                    if ser.in_waiting > 0:
                        junk = ser.read(min(ser.in_waiting, 10))
                        logger.debug(f"  무시된 바이트 (0x{first_byte_val:02x}) 후 추가 데이터 ({len(junk)}B) 비움:\n  {bytes_to_hex_pretty_str(junk)}")
                continue

            elif first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                packet_type = first_byte_val
                # 2. 제어 패킷의 시퀀스 바이트 읽기
                sequence_byte_data = ser.read(1) # ser.timeout 적용
                if sequence_byte_data:
                    sequence_num = sequence_byte_data[0]
                    type_name_str = "QUERY_SEND_REQUEST" if packet_type == QUERY_TYPE_SEND_REQUEST else f"UNKNOWN_CTRL_0x{packet_type:02x}"
                    logger.info(f"제어 패킷 수신: TYPE={type_name_str} (0x{packet_type:02x}), SEQ=0x{sequence_num:02x}")

                    if packet_type == QUERY_TYPE_SEND_REQUEST:
                        logger.debug(f"  송신 요청(SEQ=0x{sequence_num:02x})에 대해 송신 허가(PERMIT ACK) 응답 전송.")
                        _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                    else:
                        logger.warning(f"  알려진 제어 타입이지만 처리 로직이 없는 패킷 수신: 0x{packet_type:02x}")
                else:
                    logger.warning(f"제어 패킷의 시퀀스 번호 수신 실패 (TYPE=0x{packet_type:02x} 이후 데이터 없음 또는 타임아웃).")
                    if ser.in_waiting > 0:
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  제어 패킷 불완전 수신 후 버려진 데이터 ({len(junk)}B): {bytes_to_hex_pretty_str(junk)}")

            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                # first_byte_val은 LENGTH 바이트의 값 (시퀀스 1B + 압축페이로드 N B)
                actual_content_len_from_length_byte = first_byte_val 
                logger.debug(f"데이터 패킷 길이(LENGTH) 바이트 수신: {actual_content_len_from_length_byte} (0x{actual_content_len_from_length_byte:02x}) - 유효 범위 내.")

                # 2. LENGTH 바이트가 지시하는 만큼의 콘텐츠(시퀀스 + 압축페이로드) 읽기
                actual_content_bytes = ser.read(actual_content_len_from_length_byte) # ser.timeout 적용

                rssi_raw_value: Optional[int] = None
                rssi_dbm_value: Optional[int] = None # dBm은 정수 표현
                
                # 3. 콘텐츠를 성공적으로 읽었다면, RSSI 바이트 읽기 시도
                if len(actual_content_bytes) == actual_content_len_from_length_byte:
                    # RSSI 바이트는 콘텐츠 바로 뒤에 와야 함.
                    # ser.read(1)은 ser.timeout(SERIAL_READ_TIMEOUT) 및 ser.inter_byte_timeout의 영향을 받음.
                    rssi_byte_data = ser.read(1) 
                    if rssi_byte_data:
                        rssi_raw_value = rssi_byte_data[0]
                        # RSSI 값 변환 (모듈 제조사 및 모델에 따라 다를 수 있음)
                        # **주의: 실제 사용하는 LoRa 모듈의 데이터시트를 참조하여 정확한 변환 공식을 사용하세요.**
                        # 예시: Ebyte 모듈 등에서 보이는 형식 (값이 클수록 강한 신호, dBm은 0에 가까움)
                        # dBm = -(256 - RawRSSI)
                        try:
                            rssi_dbm_value = -(256 - rssi_raw_value) 
                        except TypeError: # rssi_raw_value가 None일리는 없지만 방어적으로
                            rssi_dbm_value = None

                        logger.debug(f"RSSI 바이트 수신: Raw=0x{rssi_raw_value:02x} ({rssi_raw_value}), 추정 dBm={rssi_dbm_value}")
                    else:
                        logger.debug("RSSI 바이트 수신 실패 (타임아웃 또는 데이터 없음). LoRa 모듈 설정에서 'Enable RSSI byte' 기능이 활성화되어 있는지 확인하세요.")
                
                # 이제 actual_content_bytes와 (수신된 경우) RSSI 값을 사용하여 처리
                if len(actual_content_bytes) == actual_content_len_from_length_byte:
                    actual_seq = actual_content_bytes[0]
                    payload_chunk_from_actual_frame = actual_content_bytes[1:]

                    rssi_info_str = ""
                    if rssi_raw_value is not None and rssi_dbm_value is not None:
                        rssi_info_str = f", RSSI_RAW=0x{rssi_raw_value:02x}, RSSI_dBm={rssi_dbm_value}dBm"
                    
                    # 전체 수신된 바이트 수 추정 (LENGTH 바이트 1B + 콘텐츠 길이 + RSSI 1B(있다면))
                    total_bytes_on_air_estimate = 1 + actual_content_len_from_length_byte + (1 if rssi_raw_value is not None else 0)
                    
                    logger.info(f"데이터 프레임 수신: 총수신추정={total_bytes_on_air_estimate}B, LENGTH값={actual_content_len_from_length_byte}B, FRAME_SEQ=0x{actual_seq:02x}, PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}B{rssi_info_str}")
                    
                    if logger.isEnabledFor(logging.DEBUG):
                         logger.debug(f"  수신된 페이로드 데이터 (압축됨):\n  {bytes_to_hex_pretty_str(payload_chunk_from_actual_frame)}")

                    _send_control_response(ser, actual_seq, ACK_TYPE_DATA)

                    try:
                        payload_dict = decoder.decompress_data(payload_chunk_from_actual_frame)
                        if payload_dict is None:
                            logger.error(f"메시지 (FRAME_SEQ: 0x{actual_seq:02x}): 디코딩 실패. PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}B")
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug(f"  디코딩 실패 페이로드 HEX (압축됨):\n {bytes_to_hex_pretty_str(payload_chunk_from_actual_frame)}")
                            continue

                        received_message_count += 1
                        logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{actual_seq:02x}) 디코딩 성공 ---")

                        ts_value = payload_dict.get('ts', 0.0)
                        ts_human_readable = "N/A"
                        ts_value_display = str(ts_value)
                        latency_ms = 0
                        current_recv_time = time.time()

                        try:
                            if isinstance(ts_value, (int, float)) and ts_value > 0:
                                ts_dt = datetime.datetime.fromtimestamp(ts_value)
                                ts_human_readable = ts_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                                ts_value_display = f"{ts_value:.3f}"
                                latency_ms = int((current_recv_time - ts_value) * 1000)
                        except (ValueError, TypeError, OSError) as e_ts:
                            logger.warning(f"  타임스탬프 값 ({ts_value}) 변환 중 오류: {e_ts}")
                            ts_human_readable = "N/A (Invalid Timestamp)"

                        accel = payload_dict.get('accel', {})
                        gyro = payload_dict.get('gyro', {})
                        angle = payload_dict.get('angle', {})
                        gps = payload_dict.get('gps', {})

                        def format_sensor_value(data_dict, key, fmt_str=".3f", default_val="N/A"):
                            val = data_dict.get(key)
                            if val is None or not isinstance(val, (int, float)): return default_val
                            try: return format(float(val), fmt_str)
                            except (ValueError, TypeError): return default_val

                        log_lines = [
                            f"  Timestamp: {ts_human_readable} (raw: {ts_value_display})",
                            f"  Accel (g): Ax={format_sensor_value(accel, 'ax')}, Ay={format_sensor_value(accel, 'ay')}, Az={format_sensor_value(accel, 'az')}",
                            f"  Gyro (°/s): Gx={format_sensor_value(gyro, 'gx', '.1f')}, Gy={format_sensor_value(gyro, 'gy', '.1f')}, Gz={format_sensor_value(gyro, 'gz', '.1f')}",
                            f"  Angle (°): Roll={format_sensor_value(angle, 'roll', '.1f')}, Pitch={format_sensor_value(angle, 'pitch', '.1f')}, Yaw={format_sensor_value(angle, 'yaw', '.1f')}",
                            f"  GPS: Lat={format_sensor_value(gps, 'lat', '.6f')}, Lon={format_sensor_value(gps, 'lon', '.6f')}, Alt={format_sensor_value(gps, 'altitude', '.1f')}m"
                        ]
                        # RSSI 정보가 있을 경우에만 로그 라인에 추가
                        if rssi_raw_value is not None and rssi_dbm_value is not None:
                            log_lines.append(f"  RSSI: {rssi_dbm_value} dBm (Raw: 0x{rssi_raw_value:02x})")
                        
                        for line in log_lines: logger.info(line)

                        meta_data = {
                            "recv_frame_seq": actual_seq,
                            "bytes_payload_on_wire": len(payload_chunk_from_actual_frame), # 압축된 페이로드 길이
                            "length_byte_value": actual_content_len_from_length_byte, # LENGTH 바이트가 명시한 실제 콘텐츠 길이
                            "latency_ms_sensor_to_recv": latency_ms,
                            "rssi_raw": rssi_raw_value, # 수신된 raw RSSI 값 (또는 None)
                            "rssi_dbm_estimated": rssi_dbm_value # 추정된 dBm 값 (또는 None)
                        }
                        _log_json(payload_dict, meta_data)
                        logger.info(f"  [OK#{received_message_count} FRAME_SEQ:0x{actual_seq:02x}] Latency (sensor): {latency_ms}ms. JSON 저장됨.")

                    except Exception as e_decode_process:
                        logger.error(f"메시지 처리(디코딩 후 로깅/저장) 중 오류 (FRAME_SEQ: 0x{actual_seq:02x}): {e_decode_process}", exc_info=True)
                else: # actual_content_bytes 길이가 안맞는 경우 (콘텐츠 수신 실패)
                    logger.warning(f"데이터 프레임 내용 수신 실패: 기대 {actual_content_len_from_length_byte}B, 수신 {len(actual_content_bytes)}B. 수신된 데이터: {bytes_to_hex_pretty_str(actual_content_bytes)}")
                    if ser.in_waiting > 0: # 혹시 버퍼에 남은 데이터가 있다면 비움
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  데이터 프레임 불완전 수신 후 버려진 데이터 ({len(junk)}B): {bytes_to_hex_pretty_str(junk)}")
            
            else: # IGNORED도 아니고, KNOWN_CONTROL도 아니고, VALID_LENGTH도 아닌 경우
                logger.warning(f"처리되지 않은 알 수 없는 첫 바이트 또는 데이터 길이 범위(현재 {list(VALID_DATA_PKT_LENGTH_RANGE)}) 벗어남: 0x{first_byte_val:02x} ({first_byte_val}).")
                remaining_bytes_in_buffer = ser.in_waiting
                if remaining_bytes_in_buffer > 0:
                    junk = ser.read(remaining_bytes_in_buffer) # 버퍼 비우기
                    logger.debug(f"  처리되지 않은 알 수 없는 바이트 (0x{first_byte_val:02x}) 수신 후, 버퍼에 남아있던 추가 데이터 ({len(junk)}B) 비움:\n  {bytes_to_hex_pretty_str(junk)}")
                else:
                    logger.debug(f"  처리되지 않은 알 수 없는 바이트 (0x{first_byte_val:02x}) 수신 후, 버퍼에 추가 데이터 없음.")

    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
    except Exception as e_global:
        logger.error(f"전역 예외 발생: {e_global}", exc_info=True)
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")

if __name__ == "__main__":
    # 로거 레벨 필요 시 조정
    logging.getLogger().setLevel(logging.INFO)

    # 1) LoRa 모듈 초기화 & 설정 (sx126x 생성자)
    logger.info("sx126x 모듈 초기화 및 설정 시작...")
    lora = sx126x(
        serial_num=LORA_SERIAL_PORT,
        freq=LORA_FREQ_MHZ,
        addr=LORA_ADDR,
        power=LORA_POWER_DBM,
        rssi=LORA_RSSI_ENABLE,
        air_speed=LORA_AIR_SPEED_BPS,
        net_id=LORA_NET_ID,
        buffer_size=LORA_BUFFER_SIZE,
        crypt=LORA_CRYPT_KEY,
    )
    # 설정 완료 후 lora.ser은 이미 노멀 모드 상태
    logger.info("sx126x 설정 완료. 노멀 모드로 전환됨.")

    # 2) 수신 루프 시작 (lora.ser 재사용)
    try:
        receive_loop(lora.ser)
    finally:
        # 프로그램 종료 시 GPIO 및 Serial 정리
        if lora.ser and lora.ser.is_open:
            lora.ser.close()
            logger.info("시리얼 포트 닫힘.")
        import RPi.GPIO as GPIO
        GPIO.cleanup()
