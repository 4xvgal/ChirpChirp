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
    import rx_logger
except ImportError:
    class DummyRxLogger:
        def log_rx_event(*args, **kwargs): pass
    rx_logger = DummyRxLogger()

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
EXPECTED_TOTAL_PACKETS = 200
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
    type_name_for_log_msg = {
        ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK",
        ACK_TYPE_DATA: "DATA_ACK",
        ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"
    }.get(ack_type, f"UNKNOWN_TYPE_0x{ack_type:02x}")

    try:
        s.write(ack_bytes); s.flush()
        logger.info(f"CTRL RSP TX: TYPE={type_name_for_log_msg}, SEQ=0x{seq:02x}")
        return True
    except Exception as e:
        logger.error(f"CTRL RSP TX 실패 (TYPE=0x{ack_type:02x}, SEQ=0x{seq:02x}): {e}")
        return False

def receive_loop(mode: str):
    ser: Optional[serial.Serial] = None
    try:
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT)
        ser.inter_byte_timeout = 0.02
        logger.info(f"시리얼 포트 {PORT} 열기 성공.")
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패 ({PORT}): {e}")
        return

    # --- 핸드셰이크 루프 ---
    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN 대기 중...")
        line = ser.readline()
        if line == SYN_MSG:
            logger.info(f"SYN 수신, 핸드셰이크 ACK 전송")
            if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                handshake_success = True
                logger.info("핸드셰이크 성공.")
            else:
                time.sleep(1)
        elif not line: 
            logger.warning("핸드셰이크: SYN 대기 시간 초과.")
    
    if not handshake_success:
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        if ser and ser.is_open: ser.close()
        return

    # --- 메인 수신 루프 ---
    ser.timeout = SERIAL_READ_TIMEOUT
    logger.info(f"핸드셰이크 완료. '{mode}' 모드로 데이터 수신 대기 중...")
    
    received_message_count = 0
    
    try:
        while True:
            first_byte_data = ser.read(1)
            if not first_byte_data: continue
            
            first_byte_val = first_byte_data[0]

            # --- 제어 패킷 처리 ---
            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                sequence_byte_data = ser.read(1)
                if sequence_byte_data:
                    sequence_num = sequence_byte_data[0]
                    _send_control_response(ser, sequence_num, ACK_TYPE_SEND_PERMIT)
                continue
            
            # --- 데이터 패킷 처리 ---
            elif 1 < first_byte_val <= 57: # LENGTH 바이트 (1(SEQ) + 56(MAX_PAYLOAD))
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
                    _send_control_response(ser, frame_seq, ACK_TYPE_DATA)

                    try:
                        payload_dict = decode_frame_payload(payload_chunk, mode)

                        if payload_dict:
                            if payload_dict.get("type") in ["dummy", "dummy_bam"]:
                                received_message_count += 1
                                dummy_size = payload_dict.get("size", "N/A")
                                logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{frame_seq:02x}) 더미 데이터 수신 성공 ---")
                                logger.info(f"  Type: {payload_dict.get('type')}, Size: {dummy_size}B")
                                meta = {"recv_frame_seq": frame_seq, "rssi_dbm": rssi_dbm, "type": "dummy", "size": dummy_size}
                                _log_json({"status": "dummy_received"}, meta)
                            else: # 센서 데이터
                                received_message_count += 1
                                logger.info(f"--- 메시지 #{received_message_count} (FRAME_SEQ: 0x{frame_seq:02x}) 디코딩 성공 ---")
                                ts_val = payload_dict.get('ts', 0.0)
                                latency_ms = int((time.time() - ts_val) * 1000) if ts_val > 0 else 0
                                accel, gyro, angle, gps = [payload_dict.get(k, {}) for k in ["accel", "gyro", "angle", "gps"]]
                                logger.info(f"  Latency: {latency_ms}ms, Accel(g): Ax={accel.get('ax', 0):.3f}, GPS: Lat={gps.get('lat', 0):.6f}")
                                meta = {"recv_frame_seq": frame_seq, "latency_ms": latency_ms, "rssi_dbm": rssi_dbm}
                                _log_json(payload_dict, meta)
                        else:
                            logger.error(f"메시지 (FRAME_SEQ: 0x{frame_seq:02x}): 디코딩 실패.")
                    except Exception as e_decode:
                        logger.error(f"메시지 처리 중 오류 (FRAME_SEQ: 0x{frame_seq:02x}): {e_decode}", exc_info=True)
                else:
                    logger.warning(f"데이터 프레임 내용 수신 실패: 기대 {content_len}B, 수신 {len(content_bytes)}B.")
                continue

    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
        logger.info(f"--- PDR (Packet Delivery Rate) ---")
        if EXPECTED_TOTAL_PACKETS > 0:
            pdr = (received_message_count / EXPECTED_TOTAL_PACKETS) * 100
            logger.info(f"  기대 총 패킷 수: {EXPECTED_TOTAL_PACKETS}")
            logger.info(f"  성공적으로 처리된 패킷 수: {received_message_count}")
            logger.info(f"  PDR: {pdr:.2f}%")
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")

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