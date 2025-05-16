# sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time
import logging
import serial
import struct
import datetime
from typing import Any, Dict, List, Optional, Tuple
import binascii

try:
    from .e22_config    import init_serial
    from .packetizer    import make_frames # 수정된 packetizer 사용
    from .sensor_reader import SensorReader
    from .tx_logger     import log_tx_event
except ImportError:
    try:
        from e22_config    import init_serial
        from packetizer    import make_frames # 수정된 packetizer 사용
        from sensor_reader import SensorReader
        from tx_logger     import log_tx_event
    except ImportError as e:
        print(f"모듈 임포트 실패: {e}. 프로젝트 구조 및 PYTHONPATH를 확인하세요.")
        exit(1)

# --- 상수 정의 ---
GENERIC_TIMEOUT    = 65.0
SEND_COUNT         = 10 # 테스트용 전송 횟수
RETRY_HANDSHAKE    = 3
RETRY_QUERY_PERMIT = 3
RETRY_DATA_ACK     = 3

SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00 # 타입: 0x00, 시퀀스: HANDSHAKE_ACK_SEQ (0x00)
ACK_TYPE_DATA      = 0xAA # 타입: 0xAA, 시퀀스: 해당 데이터 프레임의 시퀀스
QUERY_TYPE_SEND_REQUEST = 0x50 # 타입: 0x50, 시퀀스: 해당 데이터 프레임의 시퀀스
ACK_TYPE_SEND_PERMIT  = 0x55 # 타입: 0x55, 시퀀스: 해당 데이터 프레임의 시퀀스

ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00 # 핸드셰이크 ACK에 사용될 고정 시퀀스 번호

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ... (print_separator, _open_serial, bytes_to_hex_pretty_str, _tx_data_packet 함수는 변경 없음) ...
def print_separator(title: str, length: int = 60, char: str = '-') -> None:
    if len(title) + 2 > length:
        logger.info(f"-- {title} --")
    else:
        pad = (length - len(title) - 2) // 2
        line = char * pad + f" {title} " + char * pad
        if len(line) < length:
            line += char
        logger.info(line)

def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        s.timeout = GENERIC_TIMEOUT
        s.inter_byte_timeout = None
        time.sleep(0.1)
        return s
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 열기 실패: {e}")
        raise

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

def _tx_data_packet(s: serial.Serial, buf: bytes) -> Tuple[bool, Optional[datetime.datetime]]:
    ts_sent = None
    try:
        ts_sent = datetime.datetime.now(datetime.timezone.utc)
        written = s.write(buf)
        s.flush()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"DATA PKT TX ({len(buf)}B):\n  {bytes_to_hex_pretty_str(buf)}")
        else:
            logger.info(f"DATA PKT TX ({len(buf)}B)")
        return written == len(buf), ts_sent
    except Exception as e:
        logger.error(f"DATA PKT TX 실패: {e}")
        return False, ts_sent


# _tx_control_packet 함수는 이전 수정 사항 유지 (packet_type, seq 순서)
def _tx_control_packet(s: serial.Serial, seq: int, packet_type: int) -> bool:
    pkt_bytes = struct.pack("!BB", packet_type, seq) # TYPE, SEQ 순서
    try:
        written = s.write(pkt_bytes)
        s.flush()
        type_name = {
            # ACK_TYPE_HANDSHAKE 는 sender가 보내는것이 아님. 수신자가 보냄.
            # sender가 수신자에게 보내는 control packet은 QUERY_TYPE_SEND_REQUEST 만 있음.
            QUERY_TYPE_SEND_REQUEST: "QUERY_SEND_REQUEST",
        }.get(packet_type, f"UNKNOWN_TYPE_0x{packet_type:02x}")

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"CTRL PKT TX ({len(pkt_bytes)}B): TYPE={type_name} (0x{packet_type:02x}), SEQ={seq}\n  {bytes_to_hex_pretty_str(pkt_bytes)}")
        else:
            logger.info(f"CTRL PKT TX: TYPE={type_name} (0x{packet_type:02x}), SEQ={seq}")
        return written == len(pkt_bytes)
    except Exception as e:
        logger.error(f"CTRL PKT TX 실패 (TYPE=0x{packet_type:02x}, SEQ={seq}): {e}")
        return False

# _handshake 함수는 이전 수정 사항 유지 (ACK 수신 시 type, seq 순서)
def _handshake(s: serial.Serial) -> bool:
    print_separator("핸드셰이크 시작")
    s.timeout = GENERIC_TIMEOUT
    for attempt in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"[핸드셰이크] SYN 전송 ({attempt}/{RETRY_HANDSHAKE})")
        sent_ok, ts_syn_sent = _tx_data_packet(s, SYN_MSG)
        
        if sent_ok:
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ, # 핸드셰이크는 고정 SEQ 사용
                attempt_num=attempt,
                event_type='HANDSHAKE_SYN_SENT',
                ts_sent=ts_syn_sent
            )
        else:
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ,
                attempt_num=attempt,
                event_type='HANDSHAKE_SYN_FAIL',
                ts_sent=ts_syn_sent
            )
            logger.warning("[핸드셰이크] SYN 전송 실패, 재시도 대기 1초")
            time.sleep(1)
            continue

        logger.info(f"[핸드셰이크] ACK 대기 중 (Timeout: {s.timeout}s)...")
        ack_bytes = s.read(ACK_PACKET_LEN)
        ts_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                atype, seq = struct.unpack("!BB", ack_bytes) # TYPE, SEQ 순서
                logger.info(f"[핸드셰이크] ACK 수신: TYPE=0x{atype:02x}, SEQ={seq}")
                if atype == ACK_TYPE_HANDSHAKE and seq == HANDSHAKE_ACK_SEQ:
                    logger.info("[핸드셰이크] 성공")
                    print_separator("핸드셰이크 완료")
                    log_tx_event(
                        frame_seq=HANDSHAKE_ACK_SEQ,
                        attempt_num=attempt,
                        event_type='HANDSHAKE_ACK_OK',
                        ts_sent=ts_syn_sent,
                        ts_ack_interaction_end=ts_ack_interaction_end,
                        total_attempts_final=attempt,
                        ack_received_final=True
                    )
                    return True
                else:
                    logger.warning(f"[핸드셰이크] 잘못된 ACK 내용 (기대: TYPE=0x{ACK_TYPE_HANDSHAKE:02x}, SEQ={HANDSHAKE_ACK_SEQ})")
                    log_tx_event(
                        frame_seq=HANDSHAKE_ACK_SEQ,
                        attempt_num=attempt,
                        event_type='HANDSHAKE_ACK_INVALID',
                        ts_sent=ts_syn_sent,
                        ts_ack_interaction_end=ts_ack_interaction_end
                    )
            except struct.error:
                logger.warning(f"[핸드셰이크] ACK 언패킹 실패: {ack_bytes!r}")
                log_tx_event(
                    frame_seq=HANDSHAKE_ACK_SEQ,
                    attempt_num=attempt,
                    event_type='HANDSHAKE_ACK_UNPACK_FAIL',
                    ts_sent=ts_syn_sent,
                    ts_ack_interaction_end=ts_ack_interaction_end
                )
        else:
            logger.warning(f"[핸드셰이크] ACK 타임아웃 또는 데이터 부족 ({len(ack_bytes)}B)")
            if ack_bytes and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"  수신 바이트:\n  {bytes_to_hex_pretty_str(ack_bytes)}")
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ,
                attempt_num=attempt,
                event_type='HANDSHAKE_ACK_TIMEOUT',
                ts_sent=ts_syn_sent,
                ts_ack_interaction_end=ts_ack_interaction_end
            )
        
        if attempt == RETRY_HANDSHAKE:
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ,
                attempt_num=attempt,
                event_type='HANDSHAKE_FINAL_FAIL',
                ts_sent=None,
                ts_ack_interaction_end=ts_ack_interaction_end,
                total_attempts_final=attempt,
                ack_received_final=False
            )

    logger.error("[핸드셰이크] 최종 실패")
    print_separator("핸드셰이크 실패")
    return False

# send_data 함수 수정: 메시지 시퀀스 번호 사용
def send_data(n: int = SEND_COUNT) -> int:
    try:
        s = _open_serial()
    except Exception:
        return 0

    if not _handshake(s):
        s.close()
        return 0

    s.timeout = GENERIC_TIMEOUT
    s.inter_byte_timeout = 0.1

    try:
        sr = SensorReader()
    except Exception as e:
        logger.critical(f"SensorReader 초기화 실패: {e}")
        s.close()
        return 0

    ok_count = 0
    # 메시지 시퀀스 번호 카운터 (0-255 순환)
    # 핸드셰이크 후 첫 메시지 시퀀스를 0 또는 1로 시작할 수 있음. 여기서는 0부터 시작.
    current_message_seq_counter = 0

    print_separator(f"총 {n}회 데이터 전송 시작")

    for msg_idx in range(1, n + 1): # msg_idx는 단순히 몇 번째 메시지인지를 나타내는 인덱스
        print_separator(f"메시지 {msg_idx}/{n} (Message SEQ: {current_message_seq_counter}) 시작")
        sample = sr.get_sensor_data()

        if not sample or 'ts' not in sample:
            logger.warning(f"[메시지 {msg_idx}] 샘플 데이터 유효성 검사 실패, 건너뜀")
            # 실패 시에도 다음 메시지를 위해 시퀀스 카운터는 증가시키는 것이 좋음 (선택 사항)
            # current_message_seq_counter = (current_message_seq_counter + 1) % 256
            continue

        # packetizer.make_frames에 현재 메시지 시퀀스 번호 전달
        frames = make_frames(sample, current_message_seq_counter)
        if not frames:
            logger.warning(f"[메시지 {msg_idx}] 프레임 생성 실패, 건너뜀")
            # current_message_seq_counter = (current_message_seq_counter + 1) % 256
            continue

        # 현재 시스템은 메시지 당 프레임이 하나임
        # frames[0]이 (message_seq | payload_chunk) 형태
        raw_data_packet = bytes([len(frames[0])]) + frames[0] # LEN + (message_seq | payload_chunk)
        
        # Query, Permit, Data ACK에 사용할 시퀀스 번호는 프레임의 첫 바이트 (즉, current_message_seq_counter % 256)
        frame_seq_for_ack_handling = frames[0][0] # packetizer에서 %256 처리된 값

        # --- 1. 전송 질의 (Query) 및 허가 (Permit) 단계 ---
        query_attempts = 0
        permission_received = False
        ts_query_sent_latest = None

        while not permission_received and query_attempts < RETRY_QUERY_PERMIT:
            query_attempts += 1
            logger.info(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 질의(Query) 전송 (시도 {query_attempts}/{RETRY_QUERY_PERMIT})")
            
            ts_query_sent_latest = datetime.datetime.now(datetime.timezone.utc)
            # _tx_control_packet은 (seq, type) 순서로 인자를 받지만, 내부에서 (type, seq)로 패킹함
            query_sent_ok = _tx_control_packet(s, frame_seq_for_ack_handling, QUERY_TYPE_SEND_REQUEST)
            
            log_tx_event(
                frame_seq=frame_seq_for_ack_handling, # 실제 프레임의 시퀀스 사용
                attempt_num=query_attempts,
                event_type='QUERY_SENT' if query_sent_ok else 'QUERY_TX_FAIL',
                ts_sent=ts_query_sent_latest
            )

            if not query_sent_ok:
                logger.error(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} Query 전송 실패.")
                if query_attempts < RETRY_QUERY_PERMIT:
                    time.sleep(0.5)
                    continue
                else:
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='QUERY_FINAL_FAIL',
                        total_attempts_final=query_attempts, ack_received_final=False
                    )
                    break # Query/Permit 루프 탈출

            logger.info(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 허가(Permit) 대기 중 (Timeout: {s.timeout}s)...")
            permit_ack_bytes = s.read(ACK_PACKET_LEN)
            ts_permit_interaction_end = datetime.datetime.now(datetime.timezone.utc)

            if len(permit_ack_bytes) == ACK_PACKET_LEN:
                try:
                    permit_type, permit_seq = struct.unpack("!BB", permit_ack_bytes) # TYPE, SEQ 순서
                    if permit_type == ACK_TYPE_SEND_PERMIT and permit_seq == frame_seq_for_ack_handling:
                        logger.info(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 허가(Permit) 수신 (TYPE=0x{permit_type:02x}, SEQ={permit_seq})")
                        permission_received = True
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_OK',
                            ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end,
                            total_attempts_final=query_attempts, ack_received_final=True
                        )
                    else:
                        logger.warning(f"  잘못된 Permit ACK 수신: 기대(TYPE=0x{ACK_TYPE_SEND_PERMIT:02x}, SEQ={frame_seq_for_ack_handling}), 실제(TYPE=0x{permit_type:02x}, SEQ={permit_seq})")
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_INVALID',
                            ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                        )
                except struct.error:
                    logger.warning(f"  Permit ACK 언패킹 실패: {permit_ack_bytes!r}")
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_UNPACK_FAIL',
                        ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                    )
            else:
                logger.warning(f"  Permit ACK 타임아웃 또는 데이터 부족 ({len(permit_ack_bytes)}B)")
                log_tx_event(
                    frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_TIMEOUT',
                    ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                )
            
            if not permission_received and query_attempts == RETRY_QUERY_PERMIT:
                 log_tx_event(
                    frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_FINAL_FAIL',
                    ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end,
                    total_attempts_final=query_attempts, ack_received_final=False
                )
            elif not permission_received:
                time.sleep(1)
        
        if not permission_received:
            logger.error(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 최종 Permit 미수신. 메시지 건너뜀.")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256 # 다음 메시지를 위해 시퀀스 증가
            continue

        # --- 2. 실제 데이터 전송 및 데이터 ACK 대기 단계 ---
        data_tx_attempts = 0
        data_ack_received = False
        ts_data_sent_latest = None

        while not data_ack_received and data_tx_attempts < RETRY_DATA_ACK:
            data_tx_attempts += 1
            logger.info(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 패킷 전송 (시도 {data_tx_attempts}/{RETRY_DATA_ACK})")
            
            data_sent_ok, ts_data_sent_latest = _tx_data_packet(s, raw_data_packet)
            
            log_tx_event(
                frame_seq=frame_seq_for_ack_handling,
                attempt_num=data_tx_attempts,
                event_type='DATA_SENT' if data_sent_ok else 'DATA_TX_FAIL',
                ts_sent=ts_data_sent_latest
            )

            if not data_sent_ok:
                logger.error(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 패킷 전송 실패.")
                if data_tx_attempts < RETRY_DATA_ACK:
                    time.sleep(0.5)
                    continue
                else:
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_FINAL_FAIL',
                        total_attempts_final=data_tx_attempts, ack_received_final=False
                    )
                    break
            
            logger.info(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 ACK 대기 중 (Timeout: {s.timeout}s)...")
            data_ack_bytes = s.read(ACK_PACKET_LEN)
            ts_data_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

            if len(data_ack_bytes) == ACK_PACKET_LEN:
                try:
                    ack_type, ack_seq = struct.unpack("!BB", data_ack_bytes) # TYPE, SEQ 순서
                    if ack_type == ACK_TYPE_DATA and ack_seq == frame_seq_for_ack_handling:
                        logger.info(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 ACK 확인 성공 (TYPE=0x{ack_type:02x}, SEQ={ack_seq})")
                        data_ack_received = True
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_OK',
                            ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end,
                            total_attempts_final=data_tx_attempts, ack_received_final=True
                        )
                    else:
                        logger.warning(f"  잘못된 데이터 ACK 수신: 기대(TYPE=0x{ACK_TYPE_DATA:02x}, SEQ={frame_seq_for_ack_handling}), 실제(TYPE=0x{ack_type:02x}, SEQ={ack_seq})")
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_INVALID',
                            ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                        )
                except struct.error:
                    logger.warning(f"  데이터 ACK 언패킹 실패: {data_ack_bytes!r}")
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_UNPACK_FAIL',
                        ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                    )
            else:
                logger.warning(f"  데이터 ACK 타임아웃 또는 데이터 부족 ({len(data_ack_bytes)}B)")
                log_tx_event(
                    frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_TIMEOUT',
                    ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                )

            if not data_ack_received and data_tx_attempts == RETRY_DATA_ACK:
                log_tx_event(
                    frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_FINAL_FAIL',
                    ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end,
                    total_attempts_final=data_tx_attempts, ack_received_final=False
                )
            elif not data_ack_received:
                time.sleep(1)

        if not data_ack_received:
            logger.error(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 최종 데이터 ACK 미수신. 메시지 실패 처리.")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256 # 다음 메시지를 위해 시퀀스 증가
            continue
        
        ok_count += 1
        logger.info(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 완료 ({msg_idx}/{n})")
        print_separator(f"메시지 {msg_idx}/{n} 완료")
        
        # 성공적으로 메시지 전송 및 ACK 수신 후 다음 메시지를 위해 시퀀스 카운터 증가
        current_message_seq_counter = (current_message_seq_counter + 1) % 256
        time.sleep(1) # 다음 메시지 전송 전 지연

    print_separator(f"전체 전송 완료: {ok_count}/{n} 성공")
    if s and s.is_open:
        s.close()
    return ok_count

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.DEBUG)
    send_data(SEND_COUNT) # SEND_COUNT 만큼 전송