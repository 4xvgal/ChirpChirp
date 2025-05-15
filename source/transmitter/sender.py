# ChirpChirp/source/transmitter/sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time, logging, serial, struct
import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from .e22_config    import init_serial
    from .packetizer    import make_frames
    from .sensor_reader import SensorReader
    from .tx_logger     import log_tx_event
except ImportError as e:
    try:
        from e22_config    import init_serial
        from packetizer    import make_frames
        from sensor_reader import SensorReader
        from tx_logger     import log_tx_event
    except ImportError as e_fallback:
        print(f"모듈 임포트 실패: {e_fallback}. 프로젝트 구조 및 PYTHONPATH를 확인하세요.")
        exit(1)


HANDSHAKE_TIMEOUT = 5.0
SEND_COUNT        = 10
RETRY_HANDSHAKE   = 3
# DELAY_BETWEEN는 이제 메시지 내 프레임이 하나뿐이므로 사용되지 않음
# DELAY_BETWEEN     = 0.1

SYN_MSG       = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
ACK_PACKET_LEN = 3 # PKT_ID(1) + SEQ(1) + TYPE(1) - ACK 포맷은 유지

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

current_message_pkt_id = 0

def _get_next_pkt_id() -> int:
    global current_message_pkt_id
    pkt_id = current_message_pkt_id
    current_message_pkt_id = (current_message_pkt_id + 1) % 256
    return pkt_id

def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        s.timeout = HANDSHAKE_TIMEOUT # 핸드셰이크 ACK 대기 시간
        s.inter_byte_timeout = None # 핸드셰이크 중에는 바이트 간 타임아웃 사용 안 함
        time.sleep(0.1)
        return s
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 열기 실패: {e}")
        raise

def _tx(s: serial.Serial, buf: bytes) -> Tuple[bool, Optional[datetime.datetime]]:
    ts_sent_utc = None
    try:
        ts_sent_utc = datetime.datetime.now(datetime.timezone.utc)
        written = s.write(buf)
        s.flush()
        logger.debug(f"TX ({len(buf)}B): {buf!r}")
        return written == len(buf), ts_sent_utc
    except Exception as e:
        logger.error(f"TX 실패: {e}")
        return False, ts_sent_utc

def _handshake(s: serial.Serial, pkt_id_for_syn: int) -> bool:
    handshake_pkt_id = 0 # 핸드셰이크 ACK의 기대 PKT_ID
    handshake_seq = 0    # 핸드셰이크 ACK의 기대 SEQ

    for i in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"핸드셰이크: SYN 전송 (시도 {i}/{RETRY_HANDSHAKE})")
        syn_sent_ok, _ = _tx(s, SYN_MSG)
        
        if not syn_sent_ok:
            logger.warning("핸드셰이크: SYN 전송 실패, 1초 후 재시도")
            time.sleep(1)
            continue

        logger.info(f"핸드셰이크: ACK (PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 대기 중...")
        # 핸드셰이크 시에는 s.timeout이 HANDSHAKE_TIMEOUT으로 설정되어 있어야 함 (_open_serial에서 설정)
        # s.inter_byte_timeout = None (핸드셰이크 시)
        ack_bytes = s.read(ACK_PACKET_LEN)

        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes)
                logger.debug(f"핸드셰이크 ACK 수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}")
                
                if ack_pkt_id == handshake_pkt_id and ack_seq == handshake_seq and ack_type == ACK_TYPE_HANDSHAKE:
                    logger.info(f"핸드셰이크: 성공 (ACK 수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x})")
                    return True
                else:
                    logger.warning(f"핸드셰이크: 잘못된 ACK 내용")
            except struct.error:
                logger.warning(f"핸드셰이크: ACK 언패킹 실패. 수신 데이터: {ack_bytes!r}")
        else:
            logger.warning(f"핸드셰이크: ACK 시간 초과 또는 데이터 부족 (수신: {len(ack_bytes)}B)")
            if ack_bytes: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes!r}")

    logger.error("핸드셰이크: 최종 실패")
    return False

def send_data(n: int = SEND_COUNT) -> int:
    try:
        s = _open_serial()
    except Exception:
        return 0 # 시리얼 포트 열기 실패 시 0 반환

    if not _handshake(s, 0): # pkt_id_for_syn는 현재 사용되지 않음
        s.close()
        return 0

    # 데이터 전송 시 타임아웃 설정
    s.timeout = 1.5            # 데이터 ACK 전체 대기 시간
    s.inter_byte_timeout = 0.1 # 데이터 ACK 바이트 간 시간 초과

    try:
        sr = SensorReader()
    except Exception as e:
        logger.critical(f"SensorReader 초기화 실패: {e}. 데이터 전송 불가.")
        s.close()
        return 0

    ok_count = 0
    logger.info(f"--- 총 {n}회 데이터 전송 시작 ---")

    for msg_idx_counter in range(1, n + 1): 
        current_pkt_id_for_msg = _get_next_pkt_id()
        
        logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}) 처리 시작 ---")

        sample = sr.get_sensor_data()
   
        # 필수 키 확인 강화
        if not sample or not all(key in sample for key in ("ts", "accel", "gyro", "angle")) \
           or not isinstance(sample.get("accel"), dict) \
           or not isinstance(sample.get("gyro"), dict) \
           or not isinstance(sample.get("angle"), dict):
            # gps는 선택 사항일 수 있으므로 여기서는 필수로 체크하지 않음
            # encoder.py의 compress_data에서 gps 누락 시 처리
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 센서 데이터 구조 불완전, 건너뜀 (1초 대기)")
            time.sleep(1)
            continue
        
        # packetizer.make_frames는 [ SEQ(1B) | PAYLOAD_CHUNK ] 형태의 바이트 시퀀스 하나를 담은 리스트를 반환
        frames_content_list = make_frames(sample, current_pkt_id_for_msg) 
        
        if not frames_content_list:
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 프레임 생성 실패 (packetizer가 빈 리스트 반환), 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        # 이제 항상 프레임은 하나 (또는 없음)
        frame_data_internal = frames_content_list[0] # SEQ(1B) | PAYLOAD_CHUNK
        
        # 프레임 내용이 비어있거나 SEQ만 있는 경우 (payload가 0바이트) 처리
        # frame_data_internal의 최소 길이는 1 (SEQ만 있는 경우)
        if len(frame_data_internal) < 1: # 이론상 packetizer가 이렇게 반환하지 않음
             logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 유효하지 않은 프레임 내용 (길이 0), 건너뜀 (1초 대기)")
             time.sleep(1)
             continue


        seq_in_frame = frame_data_internal[0] # SEQ는 1 (packetizer에서 설정)
        # payload_chunk_itself = frame_data_internal[1:] # 로깅이나 디버깅에 사용 가능

        # 전송할 패킷: LEN(1B) + SEQ(1B) + PAYLOAD_CHUNK
        # len_of_seq_plus_payload = len(frame_data_internal)
        packet_to_send = bytes([len(frame_data_internal)]) + frame_data_internal

        frame_sent_successfully = False
        attempt_count_for_frame = 0
        ts_of_last_sent_attempt = None

        # 로그 메시지에서 "({frame_idx+1}/{num_total_frames})" 부분 제거 또는 수정
        log_prefix = f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}), 프레임 SEQ={seq_in_frame}:"

        while not frame_sent_successfully: # 단일 프레임에 대한 재시도 루프
            attempt_count_for_frame += 1
            logger.info(f"{log_prefix} 전송 시도 #{attempt_count_for_frame} (패킷 길이: {len(packet_to_send)}B)...")
            
            tx_ok, ts_sent_utc = _tx(s, packet_to_send)
            ts_of_last_sent_attempt = ts_sent_utc

            if tx_ok:
                log_tx_event(
                    pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                    attempt_num=attempt_count_for_frame, event_type="SENT",
                    ts_sent=ts_sent_utc
                )
            else:
                logger.warning(f"{log_prefix} TX 오류 발생 (시도 #{attempt_count_for_frame}). 0.5초 후 재시도.")
                log_tx_event(
                    pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                    attempt_num=attempt_count_for_frame, event_type="TX_FAIL",
                    ts_sent=ts_sent_utc
                )
                time.sleep(0.5)
                continue # while 루프 처음으로 돌아가 재전송

            logger.info(f"{log_prefix} ACK (PKT_ID={current_pkt_id_for_msg}, SEQ={seq_in_frame}, TYPE={ACK_TYPE_DATA:#02x}) 대기 중 (시도 #{attempt_count_for_frame})...")
            
            ack_bytes_received = s.read(ACK_PACKET_LEN)
            ts_ack_interaction_utc = datetime.datetime.now(datetime.timezone.utc)

            if len(ack_bytes_received) == ACK_PACKET_LEN:
                try:
                    ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes_received) 
                    logger.debug(f"{log_prefix} ACK 수신됨: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}") 

                    if (ack_pkt_id == current_pkt_id_for_msg and
                        ack_seq == seq_in_frame and # seq_in_frame은 이제 항상 1
                        ack_type == ACK_TYPE_DATA):
                        logger.info(f"{log_prefix} ACK 수신 성공! (시도 #{attempt_count_for_frame} 만에)")
                        frame_sent_successfully = True # 루프 종료 조건
                        log_tx_event(
                            pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                            attempt_num=attempt_count_for_frame, event_type="ACK_OK",
                            ts_sent=ts_of_last_sent_attempt,
                            ts_ack_interaction_end=ts_ack_interaction_utc,
                            total_attempts_final=attempt_count_for_frame,
                            ack_received_final=True
                        )
                    else: # 잘못된 ACK 내용
                        logger.warning(f"{log_prefix} 잘못된 ACK 내용 (시도 #{attempt_count_for_frame}). 0.2초 후 재시도.")
                        log_tx_event(
                            pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                            attempt_num=attempt_count_for_frame, event_type="ACK_INVALID",
                            ts_sent=ts_of_last_sent_attempt,
                            ts_ack_interaction_end=ts_ack_interaction_utc
                        )
                        time.sleep(0.2)
                except struct.error:
                     logger.warning(f"{log_prefix} ACK 언패킹 실패 (시도 #{attempt_count_for_frame}). 0.2초 후 재시도.")
                     log_tx_event(
                         pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                         attempt_num=attempt_count_for_frame, event_type="ACK_INVALID", 
                         ts_sent=ts_of_last_sent_attempt,
                         ts_ack_interaction_end=ts_ack_interaction_utc
                     )
                     time.sleep(0.2)
            else: # 타임아웃 또는 데이터 부족
                logger.warning(f"{log_prefix} ACK 시간 초과 또는 데이터 부족 (시도 #{attempt_count_for_frame}, 수신: {len(ack_bytes_received)}B). 0.2초 후 재시도.")
                log_tx_event(
                    pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                    attempt_num=attempt_count_for_frame, event_type="ACK_TIMEOUT",
                    ts_sent=ts_of_last_sent_attempt,
                    ts_ack_interaction_end=ts_ack_interaction_utc
                )
                if ack_bytes_received: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes_received!r}")
                time.sleep(0.2)
        
        # 메시지당 프레임이 하나이므로, 이전에 있던 DELAY_BETWEEN은 불필요
        logger.info(f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}): 프레임 전송 및 ACK 수신 완료.")

        ok_count += 1
        logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}): 전송 성공 ---")

        if msg_idx_counter < n : # 마지막 메시지가 아니면 대기
            logger.info(f"다음 메시지 처리까지 1초 대기...")
            time.sleep(1)

    logger.info(f"=== 전체 전송 완료: 총 {n}회 중 {ok_count}회 성공 ===")
    s.close()
    return ok_count

if __name__ == "__main__":
    # 테스트를 위한 SensorReader Mock (실제 환경에서는 제거하거나 조건부로 사용)
    class MockSensorReader:
        def __init__(self):
            self.timestamp = int(time.time())
            self.count = 0

        def get_sensor_data(self):
            self.timestamp += 1
            self.count += 1
            # 첫 번째 데이터는 GPS 없이, 두 번째는 모두 포함, 세 번째는 accel 없이
            if self.count == 1:
                return {
                    "ts": self.timestamp,
                    "accel": {"ax": 1.123, "ay": 2.234, "az": 3.345},
                    "gyro": {"gx": 4.12, "gy": 5.23, "gz": 6.34},
                    "angle": {"roll": 7.1, "pitch": 8.2, "yaw": 9.3},
                    # "gps": {"lat": 37.12345, "lon": 127.12345} # GPS 누락
                }
            elif self.count == 2:
                return {
                    "ts": self.timestamp,
                    "accel": {"ax": 1.0, "ay": 2.0, "az": 3.0},
                    "gyro": {"gx": 4.0, "gy": 5.0, "gz": 6.0},
                    "angle": {"roll": 7.0, "pitch": 8.0, "yaw": 9.0},
                    "gps": {"lat": 37.12345, "lon": 127.12345}
                }
            else: # self.count >= 3
                 return { # 필수 키 accel 누락 (또는 일부러 잘못된 타입)
                    "ts": self.timestamp,
                    # "accel": {"ax": 1.0, "ay": 2.0, "az": 3.0},
                    "gyro": {"gx": 4.0, "gy": 5.0, "gz": 6.0},
                    "angle": {"roll": 7.0, "pitch": 8.0, "yaw": 9.0},
                    "gps": {"lat": 37.12345, "lon": 127.12345}
                }

    # __main__에서 테스트 시에만 SensorReader를 Mock으로 교체
    # 실제 운영 시에는 이 부분은 없어야 함
    original_sensor_reader = SensorReader 
    SensorReader = MockSensorReader
    
    send_data(n=1000)

    SensorReader = original_sensor_reader # 테스트 후 원복