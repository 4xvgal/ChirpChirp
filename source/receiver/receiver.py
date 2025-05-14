# receiver.py
# -*- coding: utf-8 -*-
from __future__ import annotations # <--- 파일의 가장 첫 번째 코드로 이동 (주석/인코딩 선언 제외)

import logging
import os
import time
import json
import datetime
import statistics
import serial
import struct # struct 추가
from collections import deque # deque는 현재 코드에서 직접 사용되지 않지만, 남겨둡니다.

try:
    from packet_reassembler import PacketReassembler, PacketReassemblyError # 수정된 Reassembler
    import decoder
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0" # 예시 포트 (실제 환경에 맞게 수정 필요)
BAUD         = 9600
HANDSHAKE_TIMEOUT = 5.0    # 핸드셰이크 SYN 또는 ACK 대기 타임아웃

# FRAME_READ_TIMEOUT 값은 ser.timeout에 직접 할당되므로 별도 상수로 사용 안 함
FRAME_MAX_CONTENT_LEN = 57 # LEN 제외한 (PKT_ID+SEQ+TOTAL+PAYLOAD_CHUNK)의 최대 길이
MIN_FRAME_CONTENT_LEN = 3  # PKT_ID+SEQ+TOTAL (페이로드 0일때)


DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

# 프로토콜 메시지 및 상수 (sender.py와 일치해야 함)
SYN_MSG            = b"SYN\r\n" # 핸드셰이크용 SYN
ACK_TYPE_HANDSHAKE = 0x00      # 핸드셰이크 ACK용 TYPE
ACK_TYPE_DATA      = 0xAA      # 데이터 ACK용 TYPE
ACK_PACKET_LEN     = 3         # ACK 패킷의 고정 길이 (PKT_ID+SEQ+TYPE)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%H:%M:%S") # 시간 포맷 일관성 (선택)


logger = logging.getLogger(__name__)


def _log_json(payload: dict, meta: dict):
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def _send_ack(s: serial.Serial, pkt_id: int, seq: int, ack_type: int):
    ack_bytes = struct.pack("!BBB", pkt_id, seq, ack_type) # Network Byte Order
    try:
        s.write(ack_bytes)
        s.flush()
        logger.debug(f"ACK 전송: PKT_ID={pkt_id}, SEQ={seq}, TYPE={ack_type:#02x} (데이터: {ack_bytes!r})")
    except Exception as e:
        logger.error(f"ACK 전송 실패 (PKT_ID={pkt_id}, SEQ={seq}): {e}")


def receive_loop():
    ser = None # finally 블록에서 사용하기 위해 선언
    try:
        ser = serial.Serial(PORT, BAUD, timeout=HANDSHAKE_TIMEOUT)
        ser.inter_byte_timeout = None # 핸드셰이크 시 readline을 위해 None
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패: {e}")
        return

    # ── 핸드셰이크 ──
    handshake_pkt_id = 0 # 핸드셰이크 ACK에 사용할 PKT_ID (sender와 약속)
    handshake_seq = 0    # 핸드셰이크 ACK에 사용할 SEQ (sender와 약속)

    handshake_success = False
    while not handshake_success: # 핸드셰이크 성공할 때까지 (또는 최대 시도 후 종료)
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중...")
        line = ser.readline() 
        if line == SYN_MSG:
            logger.info(f"SYN 수신, 핸드셰이크 ACK (PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 전송")
            _send_ack(ser, handshake_pkt_id, handshake_seq, ACK_TYPE_HANDSHAKE)
            handshake_success = True # 핸드셰이크 성공 플래그
            break 
        elif not line: # 타임아웃
            logger.warning("핸드셰이크: SYN 대기 시간 초과. 재시도...")
            # 무한 루프 방지를 위해 최대 시도 횟수 등을 추가할 수 있음
            time.sleep(1) # 재시도 전 잠시 대기
        else:
            logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}. 입력 버퍼 초기화.")
            ser.reset_input_buffer() # 예상치 못한 데이터 후 버퍼 비우기
            time.sleep(0.1)

    if not handshake_success: # 이 코드는 위 로직상 도달하기 어려우나 방어적으로
        logger.error("핸드셰이크 최종 실패. 프로그램 종료.")
        if ser and ser.is_open:
            ser.close()
        return

    # 데이터 프레임 수신 설정
    ser.timeout = 0.05 # read(1) 이 블로킹되는 최대 시간 (매우 짧게)
    ser.inter_byte_timeout = 0.1 # 바이트 간 최대 간격 (read(N) 시)
    
    reassembler = PacketReassembler()
    
    # 통계용 변수 (메시지 단위로 초기화 필요)
    inter_arrival_times, frame_content_sizes = [], []
    current_message_total_bytes_frames = 0
    current_message_first_frame_time = None
    current_message_last_frame_time = None
    
    received_message_count = 0

    try:
        while True:
            try:
                # LEN 필드 (1바이트) 읽기 시도
                len_byte = ser.read(1)
                if not len_byte:
                    # 타임아웃 (정상적인 유휴 상태일 수 있음)
                    time.sleep(0.01) # CPU 과다 사용 방지를 위한 짧은 대기
                    continue
                
                frame_content_len = len_byte[0]
                
                # 유효한 LEN 값인지 검사
                if not (MIN_FRAME_CONTENT_LEN <= frame_content_len <= FRAME_MAX_CONTENT_LEN):
                    logger.warning(f"잘못된 LEN 값 수신: {frame_content_len}. 입력 버퍼 초기화 및 동기화 재시도.")
                    if ser.in_waiting > 0:
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  잘못된 LEN 후 버려진 데이터: {junk!r}")
                    # 현재 진행 중이던 메시지 관련 통계/상태 초기화
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = current_message_last_frame_time = None
                    # reassembler도 리셋하는 것이 안전할 수 있음
                    # reassembler.reset() # PacketReassembler가 내부적으로 PKT_ID 변경 시 리셋하므로,
                                        # 잘못된 LEN으로 인해 다음 프레임의 PKT_ID가 이전과 다르면 자동 리셋될 수 있음.
                                        # 명시적 리셋도 고려.
                    continue

                # LEN 만큼 프레임 내용(PKT_ID+SEQ+TOTAL+PAYLOAD) 읽기
                frame_content_bytes = ser.read(frame_content_len)

                if len(frame_content_bytes) != frame_content_len:
                    logger.warning(f"프레임 내용 수신 실패: 기대 {frame_content_len}B, 수신 {len(frame_content_bytes)}B. 데이터: {frame_content_bytes!r}")
                    if ser.in_waiting > 0: ser.read(ser.in_waiting) # 입력 버퍼 비우기
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = current_message_last_frame_time = None
                    continue
                
                # 수신된 프레임 내용 파싱 (PKT_ID, SEQ는 ACK 전송에 필요)
                frame_pkt_id = frame_content_bytes[0]
                frame_seq = frame_content_bytes[1]
                frame_total_val = frame_content_bytes[2] # 디버깅/로깅용

                logger.debug(f"프레임 수신: LEN={frame_content_len}, PKT_ID={frame_pkt_id}, SEQ={frame_seq}, TOTAL={frame_total_val}")

                # 수신한 프레임에 대해 ACK 전송
                _send_ack(ser, frame_pkt_id, frame_seq, ACK_TYPE_DATA)

                # 타이밍 통계 (메시지 단위로 관리)
                now = time.time()
                
                if current_message_first_frame_time is None:
                    current_message_first_frame_time = now
                    current_message_last_frame_time = now # 첫 프레임이므로 last도 동일
                    inter_arrival_times.clear() # 새 메시지 시작이므로 이전 inter-arrival 초기화
                    frame_content_sizes.clear() # 새 메시지 시작이므로 이전 frame_sizes 초기화
                    current_message_total_bytes_frames = 0 # 새 메시지 시작
                elif current_message_last_frame_time is not None: # 같은 메시지의 후속 프레임
                     inter_arrival_times.append((now - current_message_last_frame_time) * 1000)
                     current_message_last_frame_time = now
                
                # LEN(1B) + 프레임 내용 길이
                frame_content_sizes.append(1 + frame_content_len) 
                current_message_total_bytes_frames += (1 + frame_content_len)


                # PacketReassembler로 프레임 처리
                try:
                    complete_blob = reassembler.process_frame(frame_content_bytes)
                    
                    if complete_blob is None: # 아직 메시지 완성 전
                        continue
                    
                    # 메시지 완성!
                    received_message_count += 1
                    
                    # complete_blob이 생성된 메시지의 PKT_ID는 현재 프레임의 PKT_ID와 동일해야 함.
                    # (PacketReassembler가 PKT_ID 변경 시 리셋하므로)
                    completed_message_pkt_id = frame_pkt_id 
                    
                    payload_dict = decoder.decompress_data(complete_blob)
                    if payload_dict is None:
                        logger.error(f"메시지 #{received_message_count} (PKT_ID: {completed_message_pkt_id}): 디코딩 실패.")
                        # 메시지 처리 실패 시 관련 통계 변수 초기화
                        current_message_first_frame_time = None 
                        # (reassembler는 process_frame 성공/실패 시 내부적으로 리셋됨)
                        continue

                    # 성공적으로 메시지 수신 및 디코딩 완료
                    ts = payload_dict.get("ts", 0.0)
                    human_ts = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    accel = payload_dict.get("accel", {})
                    gyro  = payload_dict.get("gyro", {})
                    angle = payload_dict.get("angle", {})
                    gps   = payload_dict.get("gps", {})

                    logger.info(f"=== 메시지 #{received_message_count} (PKT_ID: {completed_message_pkt_id}) 수신 완료 ===")
                    logger.info(f"  Timestamp: {human_ts} (raw: {ts:.3f})")
                    logger.info(f"  Accel (g): Ax={accel.get('ax','N/A'):.3f}, Ay={accel.get('ay','N/A'):.3f}, Az={accel.get('az','N/A'):.3f}")
                    logger.info(f"  Gyro  (°/s): Gx={gyro.get('gx','N/A'):.1f}, Gy={gyro.get('gy','N/A'):.1f}, Gz={gyro.get('gz','N/A'):.1f}")
                    logger.info(f"  Angle (°): Roll={angle.get('roll','N/A'):.1f}, Pitch={angle.get('pitch','N/A'):.1f}, Yaw={angle.get('yaw','N/A'):.1f}")
                    logger.info(f"  GPS   (°): Lat={gps.get('lat','N/A'):.6f}, Lon={gps.get('lon','N/A'):.6f}")

                    # 메타 데이터 계산 (메시지 완료 시점 기준)
                    # now_complete는 blob이 생성된 후의 시간. latency 계산에 사용.
                    now_complete = time.time() 
                    latency_ms = int((now_complete - current_message_first_frame_time) * 1000) if current_message_first_frame_time else 0
                    jitter_ms = statistics.pstdev(inter_arrival_times) if len(inter_arrival_times) > 1 else 0.0
                    avg_frame_size_val = round(sum(frame_content_sizes) / len(frame_content_sizes), 2) if frame_content_sizes else 0

                    meta_data = {
                        "pkt_id": completed_message_pkt_id,
                        "bytes_compressed": len(complete_blob),
                        "latency_ms": latency_ms,
                        "jitter_ms": round(jitter_ms, 2),
                        "total_bytes_frames": current_message_total_bytes_frames,
                        "avg_frame_size": avg_frame_size_val
                    }
                    logger.info(f"  [OK#{received_message_count} PKT_ID:{completed_message_pkt_id}] Latency: {meta_data['latency_ms']}ms, Jitter: {meta_data['jitter_ms']}ms")
                    _log_json(payload_dict, meta_data)

                    # 다음 메시지 준비 (통계 변수 리셋)
                    current_message_first_frame_time = None 
                    # inter_arrival_times, frame_content_sizes는 새 메시지 첫 프레임 수신 시 초기화됨.
                    # current_message_total_bytes_frames도 마찬가지.

                except PacketReassemblyError as e:
                    logger.error(f"재조립 오류: {e}")
                    # 재조립 오류 발생 시, 현재 진행중이던 메시지 관련 통계/상태 초기화
                    current_message_first_frame_time = None
                    if ser.in_waiting > 0:
                        logger.debug(f"  재조립 오류 후 버려진 데이터: {ser.read(ser.in_waiting)!r}")
                except Exception as e_reassembly_user: # decoder.decompress_data 등에서 발생 가능
                    logger.error(f"메시지 처리(디코딩 등) 중 오류: {e_reassembly_user}")
                    current_message_first_frame_time = None # 다음 메시지 위해 초기화

            except serial.SerialTimeoutException: # ser.read(1) 또는 ser.read(N)에서 발생 가능
                logger.debug("시리얼 읽기 타임아웃 (정상 유휴 상태일 수 있음)")
                time.sleep(0.01) # CPU 사용 방지
                continue # 루프 계속
            except Exception as e_outer_loop: # LEN 읽기, 프레임 내용 읽기, ACK 전송 등에서 발생한 예외
                logger.error(f"프레임 처리 외부 루프에서 예외 발생: {e_outer_loop}", exc_info=True)
                # 심각한 오류일 수 있으므로, 잠시 대기 후 계속하거나, 또는 루프를 중단해야 할 수도 있음
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
    # logger.setLevel(logging.DEBUG) # 상세 로그 확인 시
    receive_loop()