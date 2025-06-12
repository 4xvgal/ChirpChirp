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
    import decoder # 같은 폴더에 있다고 가정
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

# --- [수정] Sender에서 사용하는 압축 방식과 동일하게 설정 ---
# 테스트하려는 모드를 sender.py와 동일하게 설정하세요.
# - 실제 압축: "zlib", "none", "bam"
# - PDR 테스트용 더미 페이로드 (크기별): "dummy_24b", "dummy_16b", "dummy_8b"
SENDER_COMPRESSION_METHOD = "none"
# --- [추가] 끝 ---

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

# --- [수정] VALID_DATA_PKT_LENGTH_RANGE를 모든 모드에 맞게 동적으로 재구성 ---
# frame_content = SEQ (1B) + payload
# 'bam' 모드 페이로드: 1B -> frame_content: 2B
# 'none' 모드 페이로드: 32B -> frame_content: 33B
# 'zlib' 모드 페이로드: 5~56B (가정) -> frame_content: 6~57B
MIN_FRAME_CONTENT_LEN_BAM = 1 + 1  # 2
MAX_FRAME_CONTENT_LEN_NONE = 1 + 32 # 33
MIN_FRAME_CONTENT_LEN_ZLIB = 1 + 5  # 6 (zlib 최소 압축 크기 가정)
MAX_FRAME_CONTENT_LEN_ZLIB = 1 + 56 # 57 (LoRa 최대 페이로드)

# 모든 가능성을 포함하는 최소/최대값
MIN_FRAME_LEN = MIN_FRAME_CONTENT_LEN_BAM
MAX_FRAME_LEN = MAX_FRAME_CONTENT_LEN_ZLIB
VALID_DATA_PKT_LENGTH_RANGE = range(MIN_FRAME_LEN, MAX_FRAME_LEN + 1)
logger.info(f"사용 압축 방식: {SENDER_COMPRESSION_METHOD}")
logger.info(f"수신 유효 데이터 프레임 콘텐츠 길이 범위(LENGTH 바이트 값): {list(VALID_DATA_PKT_LENGTH_RANGE)}")
# --- [수정] 끝 ---

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
        if ser and ser.is_open:
            ser.close()
        return

    ser.timeout = SERIAL_READ_TIMEOUT
    logger.info(f"핸드셰이크 완료. 데이터 수신 대기 중... (timeout={ser.timeout}s, inter_byte_timeout={ser.inter_byte_timeout}s)")

    received_message_count = 0
    expected_total_packets_for_pdr = EXPECTED_TOTAL_PACKETS
    
    try:
        while True:
            first_byte_data = ser.read(1)

            if not first_byte_data:
                continue

            first_byte_val = first_byte_data[0]
            
            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                packet_type = first_byte_val
                first_byte_hex_str = f"0x{packet_type:02x}"
                
                sequence_byte_data = ser.read(1)
                if sequence_byte_data:
                    sequence_num = sequence_byte_data[0]
                    type_name_str = "QUERY_SEND_REQUEST" if packet_type == QUERY_TYPE_SEND_REQUEST else f"UNKNOWN_CTRL_0x{packet_type:02x}"
                    logger.info(f"제어 패킷 수신: TYPE={type_name_str} ({first_byte_hex_str}), SEQ=0x{sequence_num:02x}")
                    rx_logger.log_rx_event(event_type="CTRL_PKT_RECV", packet_type_recv_hex=first_byte_hex_str, frame_seq_recv=sequence_num)

                    if packet_type == QUERY_TYPE_SEND_REQUEST:
                        logger.debug(f"  송신 요청(SEQ=0x{sequence_num:02x})에 대해 송신 허가(PERMIT ACK) 응답 전송.")
                        _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                else:
                    logger.warning(f"제어 패킷의 시퀀스 번호 수신 실패 (TYPE={first_byte_hex_str} 이후 데이터 없음 또는 타임아웃).")
                    rx_logger.log_rx_event(event_type="CTRL_PKT_INCOMPLETE_SEQ", packet_type_recv_hex=first_byte_hex_str)
                    if ser.in_waiting > 0:
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  제어 패킷 불완전 수신 후 버려진 데이터 ({len(junk)}B): {bytes_to_hex_pretty_str(junk)}")
                continue 

            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                actual_content_len_from_length_byte = first_byte_val 
                first_byte_hex_str = f"0x{first_byte_val:02x}"
                logger.debug(f"데이터 패킷 길이(LENGTH) 바이트 수신: {actual_content_len_from_length_byte} ({first_byte_hex_str}) - 유효 범위 내.")
                rx_logger.log_rx_event(event_type="DATA_LEN_BYTE_RECV", packet_type_recv_hex=f"LEN_{first_byte_hex_str}", data_len_byte_value=actual_content_len_from_length_byte)

                actual_content_bytes = ser.read(actual_content_len_from_length_byte)

                rssi_raw_value: Optional[int] = None
                rssi_dbm_value: Optional[int] = None
                
                if len(actual_content_bytes) == actual_content_len_from_length_byte:
                    rssi_byte_data = ser.read(1) 
                    if rssi_byte_data:
                        rssi_raw_value = rssi_byte_data[0]
                        try:
                            rssi_dbm_value = -(256 - rssi_raw_value) 
                        except TypeError:
                            rssi_dbm_value = None
                        logger.debug(f"RSSI 바이트 수신: Raw=0x{rssi_raw_value:02x} ({rssi_raw_value}), 추정 dBm={rssi_dbm_value}")
                    else:
                        logger.debug("RSSI 바이트 수신 실패 (타임아웃 또는 데이터 없음).")
                
                if len(actual_content_bytes) == actual_content_len_from_length_byte:
                    actual_seq = actual_content_bytes[0]
                    payload_chunk_from_actual_frame = actual_content_bytes[1:]

                    rssi_info_str = ""
                    if rssi_raw_value is not None and rssi_dbm_value is not None:
                        rssi_info_str = f", RSSI_dBm={rssi_dbm_value}dBm"
                    
                    logger.info(f"데이터 프레임 수신: LENGTH={actual_content_len_from_length_byte}B, FRAME_SEQ=0x{actual_seq:02x}, PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}B{rssi_info_str}")
                    rx_logger.log_rx_event(event_type="DATA_FRAME_RECV_FULL", packet_type_recv_hex="DATA_FRAME", frame_seq_recv=actual_seq, 
                                 data_len_byte_value=actual_content_len_from_length_byte, 
                                 payload_len_on_wire=len(payload_chunk_from_actual_frame), 
                                 rssi_dbm=rssi_dbm_value)
                    
                    if logger.isEnabledFor(logging.DEBUG):
                         logger.debug(f"  수신된 페이로드 데이터:\n  {bytes_to_hex_pretty_str(payload_chunk_from_actual_frame)}")

                    _send_control_response(ser, actual_seq, ACK_TYPE_DATA)

                    try:
                        # --- [핵심 수정] decoder.decode 함수 호출 ---
                        payload_dict = decoder.decode(payload_chunk_from_actual_frame, method=SENDER_COMPRESSION_METHOD)
                        # --- [핵심 수정] 끝 ---

                        if payload_dict is None:
                            logger.error(f"메시지 (FRAME_SEQ: 0x{actual_seq:02x}): 디코딩 실패. PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}B")
                            rx_logger.log_rx_event(event_type="DECODE_FAIL", frame_seq_recv=actual_seq, 
                                         payload_len_on_wire=len(payload_chunk_from_actual_frame), 
                                         notes=f"decoder.decode(method='{SENDER_COMPRESSION_METHOD}') returned None")
                        else: 
                            received_message_count += 1
                            logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{actual_seq:02x}) 디코딩 성공 ---")
                            
                            # 'bam' 디코딩 스텁 결과 처리
                            if payload_dict.get('type') == 'bam_decoded':
                                logger.info(f"  BAM 디코딩 결과 (스텁): {payload_dict}")
                                meta_data = {
                                    "recv_frame_seq": actual_seq,
                                    "compression_method": SENDER_COMPRESSION_METHOD,
                                    "bytes_payload_on_wire": len(payload_chunk_from_actual_frame),
                                    "rssi_raw": rssi_raw_value, "rssi_dbm_estimated": rssi_dbm_value
                                }
                                _log_json(payload_dict, meta_data)
                                continue # 상세 정보 로깅은 생략

                            # 'none' 또는 'zlib' 결과 처리
                            ts_value = payload_dict.get('ts', 0.0)
                            latency_ms = 0; is_ts_valid = False
                            try:
                                if isinstance(ts_value, (int, float)) and ts_value > 0:
                                    latency_ms = int((time.time() - ts_value) * 1000); is_ts_valid = True
                            except Exception: pass
                            
                            rx_logger.log_rx_event(event_type="DECODE_SUCCESS", frame_seq_recv=actual_seq,
                                         payload_len_on_wire=len(payload_chunk_from_actual_frame),
                                         decoded_ts_valid=is_ts_valid, 
                                         decoded_latency_ms=latency_ms if is_ts_valid else None,
                                         notes=f"Msg #{received_message_count}")
                            
                            # (이하 상세 정보 로깅 부분은 기존과 동일)
                            accel = payload_dict.get('accel', {})
                            gyro = payload_dict.get('gyro', {})
                            angle = payload_dict.get('angle', {})
                            gps = payload_dict.get('gps', {})

                            def format_sensor_value(data_dict, key, fmt_str=".3f", default_val="N/A"):
                                val = data_dict.get(key)
                                if val is None or not isinstance(val, (int, float)): return default_val
                                try: return format(float(val), fmt_str)
                                except (ValueError, TypeError): return default_val

                            ts_human_readable = datetime.datetime.fromtimestamp(ts_value).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if is_ts_valid else "N/A"
                            log_lines = [
                                f"  Timestamp: {ts_human_readable} (raw: {ts_value:.3f})",
                                f"  Accel (g): Ax={format_sensor_value(accel, 'ax')}, Ay={format_sensor_value(accel, 'ay')}, Az={format_sensor_value(accel, 'az')}",
                                f"  Angle (°): Roll={format_sensor_value(angle, 'roll', '.1f')}, Pitch={format_sensor_value(angle, 'pitch', '.1f')}",
                                f"  GPS: Lat={format_sensor_value(gps, 'lat', '.6f')}, Lon={format_sensor_value(gps, 'lon', '.6f')}, Alt={format_sensor_value(gps, 'altitude', '.1f')}m"
                            ]
                            for line in log_lines: logger.info(line)

                            meta_data = {
                                "recv_frame_seq": actual_seq,
                                "compression_method": SENDER_COMPRESSION_METHOD,
                                "bytes_payload_on_wire": len(payload_chunk_from_actual_frame),
                                "latency_ms_sensor_to_recv": latency_ms,
                                "rssi_raw": rssi_raw_value, "rssi_dbm_estimated": rssi_dbm_value
                            }
                            _log_json(payload_dict, meta_data)
                            logger.info(f"  [OK#{received_message_count} FRAME_SEQ:0x{actual_seq:02x}] Latency (sensor): {latency_ms}ms. JSON 저장됨.")

                    except Exception as e_decode_process:
                        logger.error(f"메시지 처리 중 오류 (FRAME_SEQ: 0x{actual_seq:02x}): {e_decode_process}", exc_info=True)
                        rx_logger.log_rx_event(event_type="DECODE_PROCESS_ERROR", frame_seq_recv=actual_seq, notes=str(e_decode_process))
                else: 
                    logger.warning(f"데이터 프레임 내용 수신 실패: 기대 {actual_content_len_from_length_byte}B, 수신 {len(actual_content_bytes)}B.")
                    rx_logger.log_rx_event(event_type="DATA_FRAME_CONTENT_FAIL", 
                                 data_len_byte_value=actual_content_len_from_length_byte, 
                                 notes=f"Expected {actual_content_len_from_length_byte}B, got {len(actual_content_bytes)}B.")
                continue 

            else:
                pass # 알 수 없는 첫 바이트는 무시

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
            logger.info(f"  기대 총 패킷 수가 0이므로 PDR을 계산할 수 없습니다.")
        # --- PDR 계산 및 출력 끝 ---

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