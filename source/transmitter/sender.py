# sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time, logging, serial, struct # struct 추가
from typing import Any, Dict, List

try:
    from e22_config    import init_serial
    # packetizer에서 MAX_PAYLOAD_CHUNK 대신 make_frames만 직접 사용
    from packetizer    import make_frames
    from sensor_reader import SensorReader
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 필요한 파일들이 올바른 위치에 있는지 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
# LORA_MAX_FRAME_SIZE = 58 # LoRa 물리 계층이 전송할 수 있는 최대 바이트 수 (가정)
# LEN_FIELD_SIZE = 1
# PKT_ID_FIELD_SIZE = 1
# SEQ_FIELD_SIZE = 1
# TOTAL_FIELD_SIZE = 1
# ACK_TYPE_FIELD_SIZE = 1

# MAX_PAYLOAD_CHUNK 는 encoder.py 에 정의됨 (54)
# 결과적으로 프레임 (PKT_ID+SEQ+TOTAL+PAYLOAD_CHUNK)의 최대 길이는 1+1+1+54 = 57 바이트.
# 여기에 LEN(1)이 붙으면 최종 전송 패킷은 최대 58 바이트.

HANDSHAKE_TIMEOUT = 5.0
SEND_COUNT        = 10 # 테스트용
RETRY_HANDSHAKE   = 3
RETRY_FRAME       = 3
DELAY_BETWEEN     = 0.1 # 테스트용

# 프로토콜 메시지 및 상수
SYN_MSG       = b"SYN\r\n" # 핸드셰이크용 SYN (예시, \r\n 사용)
ACK_TYPE_HANDSHAKE = 0x00 # 핸드셰이크 ACK용 TYPE (임의 지정)
ACK_TYPE_DATA      = 0xAA # 데이터 ACK용 TYPE (요구사항)

# ACK 패킷은 [PKT_ID(1B) | SEQ(1B) | TYPE(1B)] = 3바이트
ACK_PACKET_LEN = 3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 현재 메시지의 PKT_ID (0-255 순환)
current_message_pkt_id = 0

def _get_next_pkt_id() -> int:
    global current_message_pkt_id
    pkt_id = current_message_pkt_id
    current_message_pkt_id = (current_message_pkt_id + 1) % 256
    return pkt_id

def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        # 핸드셰이크 시에는 readline을 사용할 수 있으므로 timeout을 길게 설정
        s.timeout = HANDSHAKE_TIMEOUT 
        s.inter_byte_timeout = None # readline 사용 시 방해될 수 있으므로 None
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
    # 핸드셰이크는 SYN -> ACK(PKT_ID=pkt_id_for_syn, SEQ=0, TYPE=ACK_TYPE_HANDSHAKE) 로 가정
    # 또는 SYN -> ACK(PKT_ID=0, SEQ=0, TYPE=ACK_TYPE_HANDSHAKE) 일 수도 있음.
    # 여기서는 SYN 보낼 때 사용한 PKT_ID를 ACK에서도 기대하는 것으로 가정.
    # 만약 핸드셰이크 ACK가 항상 PKT_ID=0, SEQ=0 이라면 아래 expected_ack_pkt_id 수정.
    
    # 핸드셰이크는 간단하게 기존 SYN 보내고, 수신측에서 PKT_ID=0, SEQ=0, TYPE=0x00 으로 응답한다고 가정.
    # 즉, 핸드셰이크용 PKT_ID는 0, SEQ는 0으로 고정.
    handshake_pkt_id = 0
    handshake_seq = 0

    for i in range(1, RETRY_HANDSHAKE+1):
        logger.info(f"핸드셰이크: SYN 전송 (시도 {i}/{RETRY_HANDSHAKE})")
        if not _tx(s, SYN_MSG): # SYN_MSG는 \n 등으로 구분되는 메시지
            logger.warning("핸드셰이크: SYN 전송 실패, 1초 후 재시도")
            time.sleep(1)
            continue

        logger.info(f"핸드셰이크: ACK (PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 대기 중...")
        
        # 핸드셰이크 ACK는 고정 3바이트 바이너리라고 가정
        s.timeout = HANDSHAKE_TIMEOUT # 핸드셰이크 ACK 수신용 타임아웃
        ack_bytes = s.read(ACK_PACKET_LEN)

        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes) # Network byte order (Big Endian)
                logger.debug(f"핸드셰이크 ACK 수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}")
                
                if ack_pkt_id == handshake_pkt_id and ack_seq == handshake_seq and ack_type == ACK_TYPE_HANDSHAKE:
                    logger.info(f"핸드셰이크: 성공 (ACK 수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x})")
                    return True
                else:
                    logger.warning(f"핸드셰이크: 잘못된 ACK 내용 (수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x} "
                                   f"| 기대: PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x})")
            except struct.error:
                logger.warning(f"핸드셰이크: ACK 언패킹 실패. 수신 데이터: {ack_bytes!r}")
        else: # 타임아웃 또는 데이터 부족
            logger.warning(f"핸드셰이크: ACK 시간 초과 또는 데이터 부족 (수신 {len(ack_bytes)}/{ACK_PACKET_LEN} 바이트, 시도 {i}/{RETRY_HANDSHAKE})")
            if ack_bytes: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes!r}")

    logger.error("핸드셰이크: 최종 실패")
    return False


def send_data(n: int = SEND_COUNT) -> int:
    try:
        s = _open_serial()
    except Exception:
        return 0

    # 핸드셰이크 시 사용할 PKT_ID (여기서는 0으로 고정했지만, 필요시 동적으로 할당)
    if not _handshake(s, 0):
        s.close()
        return 0

    # 데이터 전송 시 read() 타임아웃 (ACK_PACKET_LEN 바이트를 읽기 위한 시간)
    # 이 값은 매우 짧아야 함. LoRa 왕복 시간 + 약간의 처리 시간 고려.
    s.timeout = 0.5 # 예: 500ms (ACK 대기 시간)
    s.inter_byte_timeout = 0.1 # 바이트 간 타임아웃 (read(N)이 N 바이트를 다 못받을 경우)

    sr = SensorReader()
    ok_count = 0

    logger.info(f"--- 총 {n}회 데이터 전송 시작 ---")
    for msg_idx_counter in range(1, n+1): # 메시지 번호 (1부터 시작하는 카운터)
        current_pkt_id_for_msg = _get_next_pkt_id() # 현재 메시지에 사용할 PKT_ID 할당
        
        logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}) 처리 시작 ---")
        sample = sr.get_sensor_data()
        if not sample or not all(k in sample for k in ("ts","accel","gyro","angle","gps")):
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 불완전 샘플, 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        # make_frames 호출 시 PKT_ID 전달
        frames_content = make_frames(sample, current_pkt_id_for_msg)
        if not frames_content:
            logger.warning(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 프레임 생성 실패 (빈 데이터), 건너뜀 (1초 대기)")
            time.sleep(1)
            continue

        message_fully_sent = True
        num_total_frames = len(frames_content) # 실제 생성된 프레임 수 (이 값은 각 프레임의 TOTAL 필드와 일치해야 함)
        logger.info(f"메시지 #{msg_idx_counter} (PKT_ID: {current_pkt_id_for_msg}): 총 {num_total_frames}개 프레임으로 분할됨.")

        for frame_idx, frame_data_internal in enumerate(frames_content):
            # frame_data_internal은 [PKT_ID(1B) | SEQ(1B) | TOTAL(1B) | PAYLOAD_CHUNK] 형태
            # 여기서 SEQ는 frame_data_internal[1]
            # PKT_ID는 frame_data_internal[0] (current_pkt_id_for_msg와 같아야 함)
            # TOTAL은 frame_data_internal[2] (num_total_frames와 같아야 함)
            
            actual_pkt_id_in_frame = frame_data_internal[0]
            seq_in_frame = frame_data_internal[1] # 0-based
            total_in_frame = frame_data_internal[2]

            # 검증: make_frames가 올바르게 PKT_ID, TOTAL을 설정했는지 확인 (디버깅용)
            if actual_pkt_id_in_frame != current_pkt_id_for_msg:
                logger.error(f"  프레임 데이터 오류: PKT_ID 불일치 ({actual_pkt_id_in_frame} vs {current_pkt_id_for_msg})")
                message_fully_sent = False; break
            if total_in_frame != num_total_frames:
                logger.error(f"  프레임 데이터 오류: TOTAL 불일치 ({total_in_frame} vs {num_total_frames})")
                message_fully_sent = False; break
            
            # 전송할 패킷: LEN(1B) + 프레임 데이터 (PKT_ID+SEQ+TOTAL+PAYLOAD_CHUNK)
            packet_to_send = bytes([len(frame_data_internal)]) + frame_data_internal
            frame_sent_successfully = False
            
            log_prefix = f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}), 프레임 SEQ={seq_in_frame} ({frame_idx+1}/{num_total_frames}):"

            for attempt in range(1, RETRY_FRAME+1):
                logger.info(f"{log_prefix} 전송 시도 {attempt}/{RETRY_FRAME} (패킷 길이: {len(packet_to_send)}B)...")
                if not _tx(s, packet_to_send):
                    logger.warning(f"{log_prefix} TX 오류 발생 (시도 {attempt}/{RETRY_FRAME}). 0.2초 후 재시도.")
                    time.sleep(0.2)
                    continue

                logger.info(f"{log_prefix} ACK (PKT_ID={current_pkt_id_for_msg}, SEQ={seq_in_frame}, TYPE={ACK_TYPE_DATA:#02x}) 대기 중 (시도 {attempt}/{RETRY_FRAME})...")
                
                # ACK는 고정 3바이트 바이너리
                ack_bytes_received = s.read(ACK_PACKET_LEN)

                if len(ack_bytes_received) == ACK_PACKET_LEN:
                    try:
                        ack_pkt_id, ack_seq, ack_type = struct.unpack("!BBB", ack_bytes_received) # Network byte order
                        logger.debug(f"{log_prefix} ACK 수신됨: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x}")

                        if (ack_pkt_id == current_pkt_id_for_msg and
                            ack_seq == seq_in_frame and
                            ack_type == ACK_TYPE_DATA):
                            logger.info(f"{log_prefix} ACK 수신 성공!")
                            frame_sent_successfully = True
                            break
                        else:
                            logger.warning(f"{log_prefix} 잘못된 ACK 내용 (수신: PKT_ID={ack_pkt_id}, SEQ={ack_seq}, TYPE={ack_type:#02x} "
                                           f"| 기대: PKT_ID={current_pkt_id_for_msg}, SEQ={seq_in_frame}, TYPE={ACK_TYPE_DATA:#02x})")
                    except struct.error:
                         logger.warning(f"{log_prefix} ACK 언패킹 실패. 수신 데이터: {ack_bytes_received!r}")
                else: # 타임아웃 또는 데이터 부족
                    logger.warning(f"{log_prefix} ACK 시간 초과 또는 데이터 부족 (수신 {len(ack_bytes_received)}/{ACK_PACKET_LEN} 바이트, 시도 {attempt}/{RETRY_FRAME}).")
                    if ack_bytes_received: logger.debug(f"  ㄴ 수신된 바이트: {ack_bytes_received!r}")
            
            if not frame_sent_successfully:
                logger.error(f"{log_prefix} 최종 전송/ACK 실패. 이 메시지의 나머지 프레임 전송을 중단합니다.")
                message_fully_sent = False
                break

            if frame_idx < num_total_frames - 1:
                 logger.debug(f"{log_prefix} 성공. 다음 프레임 전송까지 {DELAY_BETWEEN}초 대기...")
                 time.sleep(DELAY_BETWEEN)
            else:
                 logger.info(f"메시지 #{msg_idx_counter} (PKT_ID {current_pkt_id_for_msg}): 모든 프레임({num_total_frames}개) 전송 및 ACK 수신 완료.")

        if message_fully_sent:
            ok_count += 1
            logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}): 전송 성공 ---")
        else:
            logger.info(f"--- 메시지 #{msg_idx_counter}/{n} (PKT_ID: {current_pkt_id_for_msg}): 전송 실패 (일부 프레임 실패) ---")

        logger.info(f"다음 메시지 처리까지 1초 대기...")
        time.sleep(1)

    logger.info(f"=== 전체 전송 완료: 총 {n}회 중 {ok_count}회 성공 ===")
    s.close()
    return ok_count

if __name__ == "__main__":
    # logger.setLevel(logging.DEBUG) # 상세 로그 확인 시
    send_data(n=3) # 테스트용 전송 횟수