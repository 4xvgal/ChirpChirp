# receiver.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import time
import json
import datetime
# import statistics # 단일 프레임이므로 jitter 등 복잡한 통계는 의미가 줄어듦
import serial
import struct

try:
    # from packet_reassembler import PacketReassembler, PacketReassemblyError # 사용 안 함
    import decoder
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0"
BAUD         = 9600
HANDSHAKE_TIMEOUT = 5.0

# 새 프레임 구조: LEN(1B) + SEQ(1B) + PAYLOAD_CHUNK(가변)
# frame_content_len은 (SEQ + PAYLOAD_CHUNK)의 길이
# PAYLOAD_CHUNK 최대 56B (encoder.MAX_PAYLOAD_CHUNK)
NEW_FRAME_MAX_CONTENT_LEN = 1 + 56  # SEQ(1) + PAYLOAD_CHUNK(최대 56) = 57
NEW_MIN_FRAME_CONTENT_LEN = 1 + 0   # SEQ(1) + PAYLOAD_CHUNK(최소 0) = 1

DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
ACK_PACKET_LEN     = 3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# --- PKT_ID 관리 ---
expected_next_pkt_id = 0

def get_and_increment_expected_pkt_id() -> int:
    global expected_next_pkt_id
    current_id = expected_next_pkt_id
    expected_next_pkt_id = (expected_next_pkt_id + 1) % 256
    return current_id
# --- PKT_ID 관리 끝 ---

def _log_json(payload: dict, meta: dict): # 이 함수는 그대로 유지
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def _send_ack(s: serial.Serial, pkt_id: int, seq: int, ack_type: int):
    ack_bytes = struct.pack("!BBB", pkt_id, seq, ack_type)
    try:
        s.write(ack_bytes)
        s.flush()
        logger.debug(f"ACK 전송: PKT_ID={pkt_id}, SEQ={seq}, TYPE={ack_type:#02x} (데이터: {ack_bytes!r})")
    except Exception as e:
        logger.error(f"ACK 전송 실패 (PKT_ID={pkt_id}, SEQ={seq}): {e}")

def receive_loop():
    ser = None
    global expected_next_pkt_id

    try:
        ser = serial.Serial(PORT, BAUD, timeout=HANDSHAKE_TIMEOUT)
        ser.inter_byte_timeout = None
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패: {e}")
        return

    handshake_pkt_id = 0
    handshake_seq = 0
    handshake_success = False
    while not handshake_success:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중...")
        line = ser.readline()
        if line == SYN_MSG:
            logger.info(f"SYN 수신, 핸드셰이크 ACK (PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 전송")
            _send_ack(ser, handshake_pkt_id, handshake_seq, ACK_TYPE_HANDSHAKE)
            handshake_success = True
            expected_next_pkt_id = 0
            break
        elif not line:
            logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
            time.sleep(1)
        else:
            logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}. 입력 버퍼 초기화.")
            ser.reset_input_buffer()
            time.sleep(0.1)

    if not handshake_success:
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        if ser and ser.is_open:
            ser.close()
        return

    ser.timeout = 0.05
    ser.inter_byte_timeout = 0.1

    received_message_count = 0
    # 기존 통계 변수 중 일부는 의미가 달라지거나 없어짐
    # current_message_first_frame_time: 현재 메시지(단일 프레임) 수신 시작 시각
    # inter_arrival_times, current_message_total_bytes_frames 등은 메시지 단위로 단순화
    current_message_arrival_time = None


    try:
        while True:
            try:
                len_byte = ser.read(1)
                if not len_byte:
                    time.sleep(0.01)
                    continue

                # frame_content_len은 (SEQ + PAYLOAD_CHUNK)의 길이
                actual_content_len = len_byte[0]
                current_message_arrival_time = time.time() # LEN 바이트 수신 직후를 메시지 도착 시작으로 간주

                if not (NEW_MIN_FRAME_CONTENT_LEN <= actual_content_len <= NEW_FRAME_MAX_CONTENT_LEN):
                    logger.warning(f"잘못된 LEN 값 수신: {actual_content_len} (기대 범위: {NEW_MIN_FRAME_CONTENT_LEN}-{NEW_FRAME_MAX_CONTENT_LEN}). 입력 버퍼 초기화.")
                    if ser.in_waiting > 0:
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  잘못된 LEN 후 버려진 데이터: {junk!r}")
                    current_message_arrival_time = None # 메시지 처리 중단
                    continue

                # 실제 내용 (SEQ + PAYLOAD_CHUNK) 읽기
                actual_content_bytes = ser.read(actual_content_len)
                frame_payload_received_time = time.time() # 페이로드 수신 완료 시각

                if len(actual_content_bytes) != actual_content_len:
                    logger.warning(f"프레임 내용 수신 실패: 기대 {actual_content_len}B, 수신 {len(actual_content_bytes)}B. 데이터: {actual_content_bytes!r}")
                    if ser.in_waiting > 0: ser.read(ser.in_waiting)
                    current_message_arrival_time = None
                    continue

                actual_seq = actual_content_bytes[0]
                payload_chunk_from_actual_frame = actual_content_bytes[1:]

                current_processing_pkt_id = expected_next_pkt_id # ACK 및 처리에 사용할 PKT_ID

                logger.debug(f"프레임 수신: LEN={actual_content_len}, (예상 PKT_ID={current_processing_pkt_id}), 실제 SEQ={actual_seq}, PAYLOAD_LEN={len(payload_chunk_from_actual_frame)}")

                if actual_seq != 1:
                    logger.warning(f"비정상 SEQ 값 수신: {actual_seq} (기대값: 1). (예상 PKT_ID: {current_processing_pkt_id}).")
                    # 이 경우 ACK는 보내되, 데이터는 폐기하거나, 처리를 시도할 수 있음.
                    # 여기서는 ACK를 보내고 데이터 처리를 시도함.
                
                _send_ack(ser, current_processing_pkt_id, actual_seq, ACK_TYPE_DATA)

                # PAYLOAD_CHUNK가 바로 complete_blob임
                complete_blob = payload_chunk_from_actual_frame

                try:
                    payload_dict = decoder.decompress_data(complete_blob)
                    if payload_dict is None:
                        logger.error(f"메시지 (예상 PKT_ID: {current_processing_pkt_id}): 디코딩 실패.")
                        current_message_arrival_time = None # 실패 시 현재 메시지 정보 초기화
                        # expected_next_pkt_id는 증가하지 않음 (송신자 재전송 기대)
                        continue

                    # 성공적으로 메시지 수신 및 디코딩 완료
                    received_message_count += 1
                    get_and_increment_expected_pkt_id() # 성공 시 다음 기대 PKT_ID로 업데이트

                    # --- 데이터 출력 부분 (기존 코드 유지) ---
                    ts = payload_dict.get("ts", 0.0)
                    human_ts = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    accel = payload_dict.get("accel", {})
                    gyro  = payload_dict.get("gyro", {})
                    angle = payload_dict.get("angle", {})
                    gps   = payload_dict.get("gps", {})

                    logger.info(f"=== 메시지 #{received_message_count} (PKT_ID: {current_processing_pkt_id}) 수신 완료 ===")
                    logger.info(f"  Timestamp: {human_ts} (raw: {ts:.3f})")
                    logger.info(f"  Accel (g): Ax={accel.get('ax','N/A'):.3f}, Ay={accel.get('ay','N/A'):.3f}, Az={accel.get('az','N/A'):.3f}")
                    logger.info(f"  Gyro  (°/s): Gx={gyro.get('gx','N/A'):.1f}, Gy={gyro.get('gy','N/A'):.1f}, Gz={gyro.get('gz','N/A'):.1f}")
                    logger.info(f"  Angle (°): Roll={angle.get('roll','N/A'):.1f}, Pitch={angle.get('pitch','N/A'):.1f}, Yaw={angle.get('yaw','N/A'):.1f}")
                    if gps: # gps 딕셔너리가 실제 내용이 있을 때만 출력
                        logger.info(f"  GPS   (°): Lat={gps.get('lat','N/A'):.6f}, Lon={gps.get('lon','N/A'):.6f}")
                    else:
                        logger.info(f"  GPS   (°): 데이터 없음")
                    # --- 데이터 출력 부분 끝 ---

                    # 메타 데이터 계산 (단일 프레임 환경에 맞게 단순화)
                    latency_ms_sensor = int((frame_payload_received_time - ts) * 1000) if ts > 0 else 0
                    total_bytes_on_wire = 1 + actual_content_len # LEN + SEQ + PAYLOAD

                    meta_data = {
                        "pkt_id": current_processing_pkt_id,
                        "bytes_compressed": len(complete_blob),
                        "latency_ms_sensor": latency_ms_sensor,
                        # "jitter_ms": 0.0, # 단일 프레임이므로 jitter는 0 또는 제거
                        "total_bytes_on_wire": total_bytes_on_wire, # 실제 전송된 바이트 수
                        # "avg_frame_size": total_bytes_on_wire, # 프레임이 하나뿐
                    }
                    # 기존 로그 메시지 포맷 유지 시도
                    # logger.info(f"  [OK#{received_message_count} PKT_ID:{current_processing_pkt_id}] Latency: {meta_data['latency_ms_sensor']}ms, Jitter: {meta_data.get('jitter_ms',0.0)}ms")
                    logger.info(f"  [OK#{received_message_count} PKT_ID:{current_processing_pkt_id}] Latency (sensor): {meta_data['latency_ms_sensor']}ms")

                    _log_json(payload_dict, meta_data) # 이 부분도 그대로 유지

                    # 다음 메시지 준비
                    current_message_arrival_time = None

                except Exception as e_decode_user:
                    logger.error(f"메시지 처리(디코딩 등) 중 오류 (PKT_ID: {current_processing_pkt_id}): {e_decode_user}")
                    current_message_arrival_time = None
                    # PKT_ID는 증가하지 않음

            except serial.SerialTimeoutException:
                logger.debug("시리얼 읽기 타임아웃 (정상 유휴 상태일 수 있음)")
                time.sleep(0.01)
                continue
            except Exception as e_outer_loop:
                logger.error(f"프레임 처리 외부 루프에서 예외 발생: {e_outer_loop}", exc_info=True)
                time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
    except Exception as e_global:
        logger.error(f"전역 예외 발생: {e_global}", exc_info=True)
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")

if __name__ == "__main__":
    # logger.setLevel(logging.DEBUG)
    receive_loop()