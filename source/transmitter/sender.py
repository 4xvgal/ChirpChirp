# ChirpChirp/source/transmitter/sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time, logging, serial, struct
import datetime # datetime 임포트
from typing import Any, Dict, List, Optional, Tuple # Optional, Tuple 추가

try:
    # source.transmitter 폴더 내의 다른 모듈을 상대 경로로 임포트
    from .e22_config    import init_serial
    from .packetizer    import make_frames
    from .sensor_reader import SensorReader # SensorReader 경로 수정 (필요시)
    from .tx_logger     import log_tx_event # tx_logger 임포트
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
# RETRY_FRAME       = 3 # 무한 재시도 로직에서는 사용 안 함
DELAY_BETWEEN     = 0.1

SYN_MSG       = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
ACK_PACKET_LEN = 3

# sender.py의 일반 로깅 설정
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__) # sender.py의 로거

current_message_pkt_id = 0

def _get_next_pkt_id() -> int:
    global current_message_pkt_id
    pkt_id = current_message_pkt_id
    current_message_pkt_id = (current_message_pkt_id + 1) % 256
    return pkt_id

def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        s.timeout = HANDSHAKE_TIMEOUT
        s.inter_byte_timeout = None
        time.sleep(0.1)
        return s
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 열기 실패: {e}")
        raise

# _tx 함수가 전송 성공 여부와 전송 시각(UTC)을 반환하도록 수정
def _tx(s: serial.Serial, buf: bytes) -> Tuple[bool, Optional[datetime.datetime]]:
    ts_sent_utc = None
    try:
        ts_sent_utc = datetime.datetime.now(datetime.timezone.utc) # 전송 직전 시간 기록 (UTC)
        written = s.write(buf)
        s.flush()
        logger.debug(f"TX ({len(buf)}B): {buf!r}")
        return written == len(buf), ts_sent_utc
    except Exception as e:
        logger.error(f"TX 실패: {e}")
        # TX 실패 로그는 tx_logger에서도 기록 가능
        return False, ts_sent_utc # 실패했어도 시도한 시간은 반환할 수 있음 (또는 None)

# _handshake 함수는 tx_logger 사용 안 함 (요청사항은 데이터 프레임 로그)
def _handshake(s: serial.Serial, pkt_id_for_syn: int) -> bool:
    # ... (기존 핸드셰이크 로직, _tx 호출 시 반환값 변경 적용)
    handshake_pkt_id = 0
    handshake_seq = 0

    for i in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"핸드셰이크: SYN 전송 (시도 {i}/{RETRY_HANDSHAKE})")
        syn_sent_ok, _ = _tx(s, SYN_MSG) # _tx가 튜플을 반환하므로 두 번째 값은 무시
        if not syn_sent_ok:
            logger.warning("핸드셰이크: SYN 전송 실패, 1초 후 재시도")
            time.sleep(1)
            continue
        # ... (나머지 핸드셰이크 로직은 이전과 동일) ...
        logger.info(f"핸드셰이크: ACK (PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 대기 중...")
        s.timeout = HANDSHAKE_TIMEOUT
        ack_bytes = s.read(ACK_PACKET_LEN)

        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes)
                logger.debug(f"핸드셰이크 ACK 수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}")
                if ack_pkt_id == handshake_pkt_id and ack_seq == handshake_seq and ack_type == ACK_TYPE_HANDSHAKE:
                    logger.info(f"핸드셰이크: 성공 (ACK 수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x})")
                    return True
                else:
                    logger.warning(f"핸드셰이크: 잘못된 ACK 내용") # 상세 내용은 로그 포맷으로
            except struct.error:
                logger.warning(f"핸드셰이크: ACK 언패킹 실패. 수신 데이터: {ack_bytes!r}")
        else:
            logger.warning(f"핸드셰이크: ACK 시간 초과 또는 데이터 부족")
            if ack_bytes: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes!r}")

    logger.error("핸드셰이크: 최종 실패")
    return False


def send_data(n: int = SEND_COUNT) -> int:
    try:
        s = _open_serial()
    except Exception:
        return 0

    if not _handshake(s, 0):
        s.close()
        return 0

    s.timeout = 1.5 # 데이터 ACK 대기 시간
    s.inter_byte_timeout = 0.1

    # SensorReader 초기화 시 MPU 연결 실패하면 여기서 예외 발생하고 프로그램 종료될 수 있음
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
        # MPU 연결 실패 시 (수정된 SensorReader), get_sensor_data는 빈 MPU 데이터를 반환할 수 있음
        # 또는 초기화 시점에서 이미 프로그램이 종료되었을 수 있음.
        if not sample or not all(k in sample for k in ("ts","accel","gyro","angle","gps")) \
           or not sample.get("accel"): # accel 데이터가 없으면 (MPU 문제 가능성) 건너뜀
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 센서 데이터 불완전, 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        frames_content = make_frames(sample, current_pkt_id_for_msg)
        if not frames_content:
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 프레임 생성 실패 (빈 데이터), 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        message_fully_sent = True
        num_total_frames = len(frames_content)
        logger.info(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 총 {num_total_frames}개 프레임으로 분할됨.")

        for frame_idx, frame_data_internal in enumerate(frames_content):
            # frame_data_internal: PKT_ID(1B) | SEQ(1B) | TOTAL(1B) | PAYLOAD_CHUNK
            actual_pkt_id_in_frame = frame_data_internal[0]
            seq_in_frame = frame_data_internal[1]
            # total_in_frame = frame_data_internal[2] # 검증용

            packet_to_send = bytes([len(frame_data_internal)]) + frame_data_internal
            frame_sent_successfully = False
            attempt_count_for_frame = 0 # 현재 프레임에 대한 시도 횟수 (1부터 시작)
            
            ts_of_last_sent_attempt = None # 현재 프레임의 마지막 전송 시도 시각

            log_prefix = f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}), 프레임 SEQ={seq_in_frame} ({frame_idx+1}/{num_total_frames}):"

            while not frame_sent_successfully: # 성공할 때까지 무한 반복
                attempt_count_for_frame += 1
                logger.info(f"{log_prefix} 전송 시도 #{attempt_count_for_frame} (패킷 길이: {len(packet_to_send)}B)...")
                
                tx_ok, ts_sent_utc = _tx(s, packet_to_send)
                ts_of_last_sent_attempt = ts_sent_utc # 마지막 전송 시도 시간 업데이트

                # TX_EVENT: SENT
                if tx_ok:
                    log_tx_event(
                        pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                        attempt_num=attempt_count_for_frame, event_type="SENT",
                        ts_sent=ts_sent_utc
                    )
                else: # TX 실패
                    logger.warning(f"{log_prefix} TX 오류 발생 (시도 #{attempt_count_for_frame}). 0.5초 후 재시도.")
                    # TX_EVENT: TX_FAIL (프레임에 대한 최종 결과는 아님, 단일 시도 실패)
                    log_tx_event(
                        pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                        attempt_num=attempt_count_for_frame, event_type="TX_FAIL",
                        ts_sent=ts_sent_utc # 실패했어도 시도한 시간
                    )
                    time.sleep(0.5)
                    continue # while 루프의 처음으로 돌아가 재전송

                logger.info(f"{log_prefix} ACK (PKT_ID={current_pkt_id_for_msg}, SEQ={seq_in_frame}, TYPE={ACK_TYPE_DATA:#02x}) 대기 중 (시도 #{attempt_count_for_frame})...")
                
                ack_bytes_received = s.read(ACK_PACKET_LEN)
                ts_ack_interaction_utc = datetime.datetime.now(datetime.timezone.utc) # ACK 관련 상호작용 종료 시점

                if len(ack_bytes_received) == ACK_PACKET_LEN:
                    try:
                        ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes_received)
                        logger.debug(f"{log_prefix} ACK 수신됨: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}")

                        if (ack_pkt_id == current_pkt_id_for_msg and
                            ack_seq == seq_in_frame and
                            ack_type == ACK_TYPE_DATA):
                            logger.info(f"{log_prefix} ACK 수신 성공! (시도 #{attempt_count_for_frame} 만에)")
                            frame_sent_successfully = True # 루프 종료 조건
                            # TX_EVENT: ACK_OK (프레임 최종 성공)
                            log_tx_event(
                                pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                                attempt_num=attempt_count_for_frame, event_type="ACK_OK",
                                ts_sent=ts_of_last_sent_attempt, # 이 프레임이 성공적으로 보내진 전송 시도
                                ts_ack_interaction_end=ts_ack_interaction_utc,
                                total_attempts_final=attempt_count_for_frame,
                                ack_received_final=True
                            )
                        else: # 잘못된 ACK 내용
                            logger.warning(f"{log_prefix} 잘못된 ACK 내용 (시도 #{attempt_count_for_frame}). 0.2초 후 재시도.")
                            # TX_EVENT: ACK_INVALID (프레임 최종 결과는 아님, 단일 시도 실패)
                            log_tx_event(
                                pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                                attempt_num=attempt_count_for_frame, event_type="ACK_INVALID",
                                ts_sent=ts_of_last_sent_attempt,
                                ts_ack_interaction_end=ts_ack_interaction_utc
                            )
                            time.sleep(0.2)
                    except struct.error:
                         logger.warning(f"{log_prefix} ACK 언패킹 실패 (시도 #{attempt_count_for_frame}). 0.2초 후 재시도.")
                         # TX_EVENT: ACK_INVALID (언패킹 실패도 잘못된 ACK로 간주)
                         log_tx_event(
                             pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                             attempt_num=attempt_count_for_frame, event_type="ACK_INVALID", # 또는 "ACK_UNPACK_FAIL"
                             ts_sent=ts_of_last_sent_attempt,
                             ts_ack_interaction_end=ts_ack_interaction_utc
                         )
                         time.sleep(0.2)
                else: # 타임아웃 또는 데이터 부족
                    logger.warning(f"{log_prefix} ACK 시간 초과 또는 데이터 부족 (시도 #{attempt_count_for_frame}). 0.2초 후 재시도.")
                    # TX_EVENT: ACK_TIMEOUT (프레임 최종 결과는 아님, 단일 시도 실패)
                    log_tx_event(
                        pkt_id=current_pkt_id_for_msg, frame_seq=seq_in_frame,
                        attempt_num=attempt_count_for_frame, event_type="ACK_TIMEOUT",
                        ts_sent=ts_of_last_sent_attempt,
                        ts_ack_interaction_end=ts_ack_interaction_utc # 타임아웃 발생 시점
                    )
                    if ack_bytes_received: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes_received!r}")
                    time.sleep(0.2)
            
            # 이 지점에 도달하면 frame_sent_successfully는 항상 True임
            if frame_idx < num_total_frames - 1:
                 logger.debug(f"{log_prefix} 성공. 다음 프레임 전송까지 {DELAY_BETWEEN}초 대기...")
                 time.sleep(DELAY_BETWEEN)
            else:
                 logger.info(f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}): 모든 프레임({num_total_frames}개) 전송 및 ACK 수신 완료.")

        # message_fully_sent는 프레임 내용 오류(PKT_ID, TOTAL 검증 - 현재 코드에서는 이 검증이 빠져있음)가
        # 발생하지 않는 한, 무한 재시도로 인해 항상 True가 될 것임.
        # 만약 make_frames에서 잘못된 프레임을 만들었다면 그 전에 문제가 될 수 있음.
        # 여기서는 모든 프레임이 (결국) 성공적으로 보내졌다고 가정.
        ok_count += 1
        logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}): 전송 성공 ---")

        if msg_idx_counter < n : # 마지막 메시지가 아니면 대기
            logger.info(f"다음 메시지 처리까지 1초 대기...")
            time.sleep(1)

    logger.info(f"=== 전체 전송 완료: 총 {n}회 중 {ok_count}회 성공 ===")
    s.close()
    return ok_count

if __name__ == "__main__":

    send_data(n=3)