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
import sys
from typing import List, Optional, Dict, Any

try:
    from decoder import decode_frame_payload
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. decoder.py가 같은 폴더에 있는지 확인하세요.")
    exit(1)
try:
    from rx_logger import log_rx_event
except ImportError:
    def log_rx_event(*args, **kwargs): pass
    print("경고: rx_logger 임포트 실패. CSV 이벤트 로깅이 비활성화됩니다.")

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
EXPECTED_TOTAL_PACKETS = 100
KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST]
DATA_DIR = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
    if not data_bytes: return "<empty>"
    hex_str = binascii.hexlify(data_bytes).decode('ascii')
    return "\n  ".join(' '.join(hex_str[i:i+j*2]) for i in range(0, len(hex_str), bytes_per_line*2) for j in range(bytes_per_line) if i+j*2 < len(hex_str))

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
    }.get(ack_type, f"UNKNOWN_TYPE_{ack_type_hex_str}")

    try:
        s.write(ack_bytes); s.flush()
        logger.info(f"CTRL RSP TX: TYPE={type_name_for_log_msg}, SEQ=0x{seq:02x}")
        log_rx_event(event_type=f"{type_name_for_log_msg}_SENT", ack_seq_sent=seq, ack_type_sent_hex=ack_type_hex_str)
        return True
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (TYPE={ack_type_hex_str}, SEQ=0x{seq:02x}): {e}")
        log_rx_event(event_type=f"{type_name_for_log_msg}_FAIL", ack_seq_sent=seq, ack_type_sent_hex=ack_type_hex_str, notes=str(e))
        return False

# --- ### 로직 복원 및 개선 부분 ### ---
def _print_sensor_data(payload: Dict[str, Any], meta: Dict[str, Any]):
    """디코딩된 센서 데이터와 메타 정보를 포맷에 맞춰 콘솔에 출력합니다."""
    ts_val = payload.get('ts', 0.0)
    
    # 안전하게 데이터 추출
    accel = payload.get('accel', {})
    gyro = payload.get('gyro', {})
    angle = payload.get('angle', {})
    gps = payload.get('gps', {})
    
    # 메타 정보 추출
    latency_ms = meta.get("latency_ms", "N/A")
    rssi_dbm = meta.get("rssi_dbm", "N/A")
    
    # 출력할 로그 라인 생성
    log_lines = [
        "----------------------------------------------------------",
        f"  Timestamp: {datetime.datetime.fromtimestamp(ts_val).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (Latency: {latency_ms}ms)",
        f"  Accel(g):  Ax={accel.get('ax', 0):.3f}, Ay={accel.get('ay', 0):.3f}, Az={accel.get('az', 0):.3f}",
        f"  Gyro(°/s): Gx={gyro.get('gx', 0):.1f}, Gy={gyro.get('gy', 0):.1f}, Gz={gyro.get('gz', 0):.1f}",
        f"  Angle(°):  Roll={angle.get('roll', 0):.1f}, Pitch={angle.get('pitch', 0):.1f}, Yaw={angle.get('yaw', 0):.1f}",
        f"  GPS:       Lat={gps.get('lat', 0):.6f}, Lon={gps.get('lon', 0):.6f}, Alt={gps.get('altitude', 0):.1f}m",
        f"  RSSI:      {rssi_dbm} dBm" if rssi_dbm is not None else "  RSSI:      N/A",
        "----------------------------------------------------------"
    ]
    
    # 생성된 모든 라인을 로깅
    for line in log_lines:
        logger.info(line)
# --- ### 로직 복원 끝 ### ---

def receive_loop(mode: str):
    ser: Optional[serial.Serial] = None
    try:
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = 0.02
        logger.info(f"시리얼 포트 {PORT} 열기 성공.")
        log_rx_event(event_type="SERIAL_PORT_OPEN")
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패 ({PORT}): {e}")
        log_rx_event(event_type="SERIAL_PORT_FAIL", notes=str(e))
        return

    # --- 핸드셰이크 루프 (변경 없음) ---
    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN 대기 중...")
        line = ser.readline()
        if line == SYN_MSG:
            logger.info(f"SYN 수신, 핸드셰이크 ACK 전송")
            log_rx_event(event_type="HANDSHAKE_SYN_RECV")
            if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                handshake_success = True
                logger.info("핸드셰이크 성공.")
                log_rx_event(event_type="HANDSHAKE_SUCCESS")
        elif not line: 
            logger.warning("핸드셰이크: SYN 대기 시간 초과.")
            log_rx_event(event_type="HANDSHAKE_SYN_TIMEOUT")

    # --- 메인 수신 루프 ---
    ser.timeout = SERIAL_READ_TIMEOUT
    logger.info(f"핸드셰이크 완료. '{mode}' 모드로 데이터 수신 대기 중...")
    
    received_message_count = 0
    
    try:
        while True:
            first_byte_data = ser.read(1)
            if not first_byte_data: continue
            
            first_byte_val = first_byte_data[0]
            first_byte_hex = f"0x{first_byte_val:02x}"

            # --- 제어 패킷 처리 (변경 없음) ---
            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                sequence_byte_data = ser.read(1)
                if sequence_byte_data:
                    sequence_num = sequence_byte_data[0]
                    log_rx_event(event_type="CTRL_PKT_RECV", frame_seq_recv=sequence_num, packet_type_recv_hex=first_byte_hex)
                    _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                continue
            
            # --- 데이터 패킷 처리 ---
            elif 1 < first_byte_val <= 57: # LENGTH 바이트
                content_len = first_byte_val
                content_bytes = ser.read(content_len)
                
                rssi_dbm = None
                if len(content_bytes) == content_len:
                    rssi_byte = ser.read(1)
                    if rssi_byte: rssi_dbm = -(256 - rssi_byte[0])
                
                if len(content_bytes) == content_len:
                    frame_seq = content_bytes[0]
                    payload_chunk = content_bytes[1:]
                    
                    logger.info(f"데이터 프레임 수신: LENGTH={content_len}B, FRAME_SEQ=0x{frame_seq:02x}, PAYLOAD_LEN={len(payload_chunk)}B, RSSI={rssi_dbm}dBm")
                    log_rx_event(event_type="DATA_FRAME_RECV", frame_seq_recv=frame_seq, rssi_dbm=rssi_dbm, data_len_byte_value=content_len, payload_len_on_wire=len(payload_chunk))
                    _send_control_response(ser, frame_seq, ACK_TYPE_DATA)

                    try:
                        payload_dict = decode_frame_payload(payload_chunk, mode)

                        if payload_dict:
                            # 더미 데이터 처리
                            if payload_dict.get("type") in ["dummy", "dummy_bam"]:
                                received_message_count += 1
                                dummy_size = payload_dict.get("size", "N/A")
                                logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{frame_seq:02x}) 더미 데이터 수신 성공 ---")
                                logger.info(f"  Type: {payload_dict.get('type')}, Size: {dummy_size}B")
                                log_rx_event(event_type="DECODE_SUCCESS_DUMMY", frame_seq_recv=frame_seq, rssi_dbm=rssi_dbm, notes=f"type: {payload_dict.get('type')}, size: {dummy_size}")
                                meta = {"recv_frame_seq": frame_seq, "rssi_dbm": rssi_dbm, "type": "dummy", "size": dummy_size}
                                _log_json({"status": "dummy_received"}, meta)
                            
                            # 센서 데이터 처리
                            else:
                                received_message_count += 1
                                logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{frame_seq:02x}) 디코딩 성공 ---")
                                
                                ts_val = payload_dict.get('ts', 0.0)
                                is_ts_valid = ts_val > 0
                                latency_ms = int((time.time() - ts_val) * 1000) if is_ts_valid else -1
                                
                                # 메타데이터 생성
                                meta = {"recv_frame_seq": frame_seq, "latency_ms": latency_ms, "rssi_dbm": rssi_dbm}
                                
                                # ### 로직 복원 및 개선 부분 ###
                                # 콘솔에 상세 데이터 출력
                                _print_sensor_data(payload_dict, meta)
                                
                                # CSV 로거 호출
                                log_rx_event(
                                    event_type="DECODE_SUCCESS",
                                    frame_seq_recv=frame_seq,
                                    rssi_dbm=rssi_dbm,
                                    is_decoded_ts_valid=is_ts_valid,
                                    calculated_latency_ms=latency_ms,
                                    decoded_payload_dict=payload_dict
                                )
                                # JSON 파일 로깅
                                _log_json(payload_dict, meta)
                        else:
                            logger.error(f"메시지 (FRAME_SEQ: 0x{frame_seq:02x}): 디코딩 실패.")
                            log_rx_event(event_type="DECODE_FAIL", frame_seq_recv=frame_seq, rssi_dbm=rssi_dbm)
                    except Exception as e_decode:
                        logger.error(f"메시지 처리 중 오류 (FRAME_SEQ: 0x{frame_seq:02x}): {e_decode}", exc_info=True)
                        log_rx_event(event_type="DECODE_EXCEPTION", frame_seq_recv=frame_seq, rssi_dbm=rssi_dbm, notes=str(e_decode))
                else:
                    logger.warning(f"데이터 프레임 내용 수신 실패: 기대 {content_len}B, 수신 {len(content_bytes)}B.")
                    log_rx_event(event_type="DATA_FRAME_INCOMPLETE", data_len_byte_value=content_len)
                continue

    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
        log_rx_event(event_type="KEYBOARD_INTERRUPT")
        logger.info(f"--- PDR (Packet Delivery Rate) ---")
        if EXPECTED_TOTAL_PACKETS > 0:
            pdr = (received_message_count / EXPECTED_TOTAL_PACKETS) * 100
            logger.info(f"  PDR: {pdr:.2f}% ({received_message_count}/{EXPECTED_TOTAL_PACKETS})")
            log_rx_event(event_type="PDR_CALCULATED", notes=f"{pdr:.2f}% ({received_message_count}/{EXPECTED_TOTAL_PACKETS})")
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")
            log_rx_event(event_type="SERIAL_PORT_CLOSED")

if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    
    if len(sys.argv) != 2:
        print("사용법: python receiver.py <mode>")
        print("  <mode>: raw, bam")
        sys.exit(1)

    rx_mode_arg = sys.argv[1].lower()
    if rx_mode_arg not in ['raw', 'bam']:
        print(f"오류: 잘못된 모드 '{rx_mode_arg}'. 'raw' 또는 'bam'을 사용하세요.")
        sys.exit(1)
        
    receive_loop(mode=rx_mode_arg)
