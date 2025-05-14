# sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time, logging, serial, struct
from typing import Any, Dict, List

try:
    from e22_config    import init_serial
    from packetizer    import make_frames
    from sensor_reader import SensorReader
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 필요한 파일들이 올바른 위치에 있는지 확인하세요.")
    exit(1)


HANDSHAKE_TIMEOUT = 5.0
SEND_COUNT        = 10
RETRY_HANDSHAKE   = 3
# RETRY_FRAME       = 3 # 더 이상 데이터 프레임 재시도 횟수 제한에 사용되지 않음 (무한 재시도)
DELAY_BETWEEN     = 0.1

SYN_MSG       = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
ACK_PACKET_LEN = 3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
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
        s.timeout = HANDSHAKE_TIMEOUT
        s.inter_byte_timeout = None
        time.sleep(0.1)
        return s
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 열기 실패: {e}")
        raise

def _tx(s: serial.Serial, buf: bytes) -> bool:
    try:
        written = s.write(buf)
        s.flush()
        logger.debug(f"TX ({len(buf)}B): {buf!r}")
        return written == len(buf)
    except Exception as e:
        logger.error(f"TX 실패: {e}")
        return False

def _handshake(s: serial.Serial, pkt_id_for_syn: int) -> bool:
    handshake_pkt_id = 0
    handshake_seq = 0

    for i in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"핸드셰이크: SYN 전송 (시도 {i}/{RETRY_HANDSHAKE})")
        if not _tx(s, SYN_MSG):
            logger.warning("핸드셰이크: SYN 전송 실패, 1초 후 재시도")
            time.sleep(1)
            continue

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
                    logger.warning(f"핸드셰이크: 잘못된 ACK 내용 (수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x} "
                                   f"| 기대: PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x})")
            except struct.error:
                logger.warning(f"핸드셰이크: ACK 언패킹 실패. 수신 데이터: {ack_bytes!r}")
        else:
            logger.warning(f"핸드셰이크: ACK 시간 초과 또는 데이터 부족 (수신 {len(ack_bytes)}/{ACK_PACKET_LEN} 바이트, 시도 {i}/{RETRY_HANDSHAKE})")
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

    s.timeout = 1.5
    s.inter_byte_timeout = 0.1

    sr = SensorReader()
    ok_count = 0

    logger.info(f"--- 총 {n}회 데이터 전송 시작 ---")
    for msg_idx_counter in range(1, n + 1):
        current_pkt_id_for_msg = _get_next_pkt_id()
        
        logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}) 처리 시작 ---")
        sample = sr.get_sensor_data()
        if not sample or not all(k in sample for k in ("ts","accel","gyro","angle","gps")):
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 불완전 샘플, 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        frames_content = make_frames(sample, current_pkt_id_for_msg)
        if not frames_content:
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 프레임 생성 실패 (빈 데이터), 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        message_fully_sent = True # 메시지 내 모든 프레임이 성공적으로 보내졌는지 추적
        num_total_frames = len(frames_content)
        logger.info(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 총 {num_total_frames}개 프레임으로 분할됨.")

        for frame_idx, frame_data_internal in enumerate(frames_content):
            actual_pkt_id_in_frame = frame_data_internal[0]
            seq_in_frame = frame_data_internal[1]
            total_in_frame = frame_data_internal[2]

            if actual_pkt_id_in_frame != current_pkt_id_for_msg:
                logger.error(f"  프레임 데이터 오류: PKT_ID 불일치 ({actual_pkt_id_in_frame} vs {current_pkt_id_for_msg}). 이 메시지 전송 중단.")
                message_fully_sent = False; break 
            if total_in_frame != num_total_frames:
                logger.error(f"  프레임 데이터 오류: TOTAL 불일치 ({total_in_frame} vs {num_total_frames}). 이 메시지 전송 중단.")
                message_fully_sent = False; break
            
            packet_to_send = bytes([len(frame_data_internal)]) + frame_data_internal
            frame_sent_successfully = False
            attempt_count = 0 # 현재 프레임에 대한 시도 횟수

            log_prefix = f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}), 프레임 SEQ={seq_in_frame} ({frame_idx+1}/{num_total_frames}):"

            # --- 변경된 부분 시작 ---
            while not frame_sent_successfully: # 성공할 때까지 무한 반복
                attempt_count += 1
                logger.info(f"{log_prefix} 전송 시도 #{attempt_count} (패킷 길이: {len(packet_to_send)}B)...")
                
                if not _tx(s, packet_to_send):
                    logger.warning(f"{log_prefix} TX 오류 발생 (시도 #{attempt_count}). 0.5초 후 재시도.")
                    time.sleep(0.5) # TX 실패 시 잠시 대기 후 재시도
                    continue # while 루프의 처음으로 돌아가 재전송

                logger.info(f"{log_prefix} ACK (PKT_ID={current_pkt_id_for_msg}, SEQ={seq_in_frame}, TYPE={ACK_TYPE_DATA:#02x}) 대기 중 (시도 #{attempt_count})...")
                
                ack_bytes_received = s.read(ACK_PACKET_LEN)

                if len(ack_bytes_received) == ACK_PACKET_LEN:
                    try:
                        ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes_received)
                        logger.debug(f"{log_prefix} ACK 수신됨: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}")

                        if (ack_pkt_id == current_pkt_id_for_msg and
                            ack_seq == seq_in_frame and
                            ack_type == ACK_TYPE_DATA):
                            logger.info(f"{log_prefix} ACK 수신 성공! (시도 #{attempt_count} 만에)")
                            frame_sent_successfully = True # 루프 종료 조건 충족
                            # 성공했으므로 break나 continue 필요 없음, while 조건에 의해 루프 탈출
                        else:
                            logger.warning(f"{log_prefix} 잘못된 ACK 내용 (수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x} "
                                           f"| 기대: PKT_ID={current_pkt_id_for_msg}, SEQ={seq_in_frame}, TYPE={ACK_TYPE_DATA:#02x}) (시도 #{attempt_count}). 0.2초 후 재시도.")
                            time.sleep(0.2) # 잘못된 ACK 수신 시 잠시 대기 후 재시도
                            # continue는 필요 없음, while 루프가 다시 돌 것임
                    except struct.error:
                         logger.warning(f"{log_prefix} ACK 언패킹 실패. 수신 데이터: {ack_bytes_received!r} (시도 #{attempt_count}). 0.2초 후 재시도.")
                         time.sleep(0.2) # 언패킹 실패 시 잠시 대기 후 재시도
                else: # 타임아웃 또는 데이터 부족
                    logger.warning(f"{log_prefix} ACK 시간 초과 또는 데이터 부족 (수신 {len(ack_bytes_received)}/{ACK_PACKET_LEN} 바이트, 시도 #{attempt_count}). 0.2초 후 재시도.")
                    if ack_bytes_received: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes_received!r}")
                    time.sleep(0.2) # ACK 타임아웃 시 잠시 대기 후 재시도
            # --- 변경된 부분 끝 ---
            
            # 이 지점에 도달하면 frame_sent_successfully는 항상 True임 (무한 재시도했으므로)
            # 따라서, 이전의 'if not frame_sent_successfully: break' 로직은 필요 없음.
            # 메시지 전체 실패는 프레임 내용 오류 (PKT_ID, TOTAL 불일치) 시에만 발생

            if frame_idx < num_total_frames - 1:
                 logger.debug(f"{log_prefix} 성공. 다음 프레임 전송까지 {DELAY_BETWEEN}초 대기...")
                 time.sleep(DELAY_BETWEEN)
            else:
                 logger.info(f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}): 모든 프레임({num_total_frames}개) 전송 및 ACK 수신 완료.")

        if message_fully_sent: # PKT_ID/TOTAL 검사 통과 및 모든 프레임 (결국) 전송 완료
            ok_count += 1
            logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}): 전송 성공 ---")
        else: # PKT_ID 또는 TOTAL 불일치로 인해 메시지 전송이 중단된 경우
            logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}): 전송 실패 (프레임 내부 데이터 오류) ---")

        logger.info(f"다음 메시지 처리까지 1초 대기...")
        time.sleep(1)

    logger.info(f"=== 전체 전송 완료: 총 {n}회 중 {ok_count}회 성공 ===")
    s.close()
    return ok_count

if __name__ == "__main__":
    # logger.setLevel(logging.DEBUG)
    send_data(n=3)