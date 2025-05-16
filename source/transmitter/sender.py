# sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time
import logging
import serial
import struct
import datetime
from typing import Any, Dict, List, Optional, Tuple # List, Optional, Tuple 임포트 추가
import binascii

try:
    from .e22_config    import init_serial
    from .packetizer    import make_frames
    from .sensor_reader import SensorReader
    from .tx_logger     import log_tx_event # tx_logger 임포트 복구
except ImportError:
    try:
        from e22_config    import init_serial
        from packetizer    import make_frames
        from sensor_reader import SensorReader
        from tx_logger     import log_tx_event # tx_logger 임포트 복구
    except ImportError as e:
        print(f"모듈 임포트 실패: {e}. 프로젝트 구조 및 PYTHONPATH를 확인하세요.")
        exit(1)

# --- 상수 정의 ---
GENERIC_TIMEOUT    = 65.0
SEND_COUNT         = 10
RETRY_HANDSHAKE    = 3
RETRY_QUERY_PERMIT = 3
RETRY_DATA_ACK     = 3

SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55

ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

# _tx_control_packet 함수 수정: packet_type과 seq의 순서 변경
def _tx_control_packet(s: serial.Serial, seq: int, packet_type: int) -> bool:
    # 변경된 부분: struct.pack("!BB", packet_type, seq)
    pkt_bytes = struct.pack("!BB", packet_type, seq)
    try:
        written = s.write(pkt_bytes)
        s.flush()
        type_name = {
            ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK", # 수신자가 보내는 것
            QUERY_TYPE_SEND_REQUEST: "QUERY_SEND_REQUEST", # 송신자가 보내는 것
            # ACK_TYPE_DATA 와 ACK_TYPE_SEND_PERMIT 은 sender가 보내는 것이 아니므로 여기서 정의할 필요 없음
        }.get(packet_type, f"UNKNOWN_TYPE_0x{packet_type:02x}")

        # 로그 메시지에서 TYPE과 SEQ 순서도 일관성 있게 표시 (선택 사항, 가독성 향상)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"CTRL PKT TX ({len(pkt_bytes)}B): TYPE={type_name} (0x{packet_type:02x}), SEQ={seq}\n  {bytes_to_hex_pretty_str(pkt_bytes)}")
        else:
            logger.info(f"CTRL PKT TX: TYPE={type_name} (0x{packet_type:02x}), SEQ={seq}")
        return written == len(pkt_bytes)
    except Exception as e:
        logger.error(f"CTRL PKT TX 실패 (TYPE=0x{packet_type:02x}, SEQ={seq}): {e}") # 로그 순서 변경
        return False

def _handshake(s: serial.Serial) -> bool:
    print_separator("핸드셰이크 시작")
    s.timeout = GENERIC_TIMEOUT
    for attempt in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"[핸드셰이크] SYN 전송 ({attempt}/{RETRY_HANDSHAKE})")
        sent_ok, ts_syn_sent = _tx_data_packet(s, SYN_MSG)
        
        if sent_ok:
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ,
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
        ack_bytes = s.read(ACK_PACKET_LEN) # ACK_PACKET_LEN은 여전히 2
        ts_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                # ACK 수신 시 언패킹 순서도 변경: atype, seq
                atype, seq = struct.unpack("!BB", ack_bytes)
                logger.info(f"[핸드셰이크] ACK 수신: TYPE=0x{atype:02x}, SEQ={seq}")
                if atype == ACK_TYPE_HANDSHAKE and seq == HANDSHAKE_ACK_SEQ: # 타입 먼저, 시퀀스 나중
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
                    logger.warning(f"[핸드셰이크] 잘못된 ACK 내용 (기대: TYPE=0x{ACK_TYPE_HANDSHAKE:02x}, SEQ={HANDSHAKE_ACK_SEQ})") # 기대값 순서 변경
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
    print_separator(f"총 {n}회 데이터 전송 시작")

    for msg_idx in range(1, n + 1):
        print_separator(f"메시지 {msg_idx}/{n} 시작")
        sample = sr.get_sensor_data()

        if not sample or 'ts' not in sample:
            logger.warning(f"[메시지 {msg_idx}] 샘플 데이터 유효성 검사 실패, 건너뜀")
            continue

        frames = make_frames(sample, 0)
        if not frames:
            logger.warning(f"[메시지 {msg_idx}] 프레임 생성 실패, 건너뜀")
            continue

        for frame_content in frames:
            raw_data_packet = bytes([len(frame_content)]) + frame_content
            frame_seq_to_send = frame_content[0] # 데이터 프레임의 SEQ는 그대로 유지

            # --- 1. 전송 질의 (Query) 및 허가 (Permit) 단계 ---
            query_attempts = 0
            permission_received = False
            ts_query_sent_latest = None

            while not permission_received and query_attempts < RETRY_QUERY_PERMIT:
                query_attempts += 1
                # Query 전송 시 seq와 packet_type 인자 순서는 _tx_control_packet 내부에서 처리됨
                logger.info(f"[메시지 {msg_idx}] Frame_SEQ={frame_seq_to_send} 전송 질의(Query) 전송 (시도 {query_attempts}/{RETRY_QUERY_PERMIT})")
                
                ts_query_sent_latest = datetime.datetime.now(datetime.timezone.utc)
                query_sent_ok = _tx_control_packet(s, frame_seq_to_send, QUERY_TYPE_SEND_REQUEST)
                
                log_tx_event(
                    frame_seq=frame_seq_to_send,
                    attempt_num=query_attempts,
                    event_type='QUERY_SENT' if query_sent_ok else 'QUERY_TX_FAIL',
                    ts_sent=ts_query_sent_latest
                )

                if not query_sent_ok:
                    logger.error(f"  Frame_SEQ={frame_seq_to_send} Query 전송 실패.")
                    if query_attempts < RETRY_QUERY_PERMIT:
                        logger.info(f"  짧은 지연 후 Query 재전송 시도...")
                        time.sleep(0.5)
                        continue
                    else:
                        logger.error(f"  Frame_SEQ={frame_seq_to_send} Query 최종 전송 실패. 메시지 건너뜀.")
                        log_tx_event(
                            frame_seq=frame_seq_to_send, attempt_num=query_attempts, event_type='QUERY_FINAL_FAIL',
                            total_attempts_final=query_attempts, ack_received_final=False
                        )
                        break

                logger.info(f"  Frame_SEQ={frame_seq_to_send} 전송 허가(Permit) 대기 중 (Timeout: {s.timeout}s)...")
                permit_ack_bytes = s.read(ACK_PACKET_LEN)
                ts_permit_interaction_end = datetime.datetime.now(datetime.timezone.utc)

                if len(permit_ack_bytes) == ACK_PACKET_LEN:
                    try:
                        # Permit ACK 수신 시 언패킹 순서 변경: permit_type, permit_seq
                        permit_type, permit_seq = struct.unpack("!BB", permit_ack_bytes)
                        # 조건문 순서 변경: 타입 먼저, 시퀀스 나중
                        if permit_type == ACK_TYPE_SEND_PERMIT and permit_seq == frame_seq_to_send:
                            logger.info(f"  Frame_SEQ={frame_seq_to_send} 전송 허가(Permit) 수신 (TYPE=0x{permit_type:02x}, SEQ={permit_seq})") # 로그 추가
                            permission_received = True
                            log_tx_event(
                                frame_seq=frame_seq_to_send, attempt_num=query_attempts, event_type='PERMIT_ACK_OK',
                                ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end,
                                total_attempts_final=query_attempts, ack_received_final=True
                            )
                        else:
                            logger.warning(f"  잘못된 Permit ACK 수신: 기대(TYPE=0x{ACK_TYPE_SEND_PERMIT:02x}, SEQ={frame_seq_to_send}), 실제(TYPE=0x{permit_type:02x}, SEQ={permit_seq})") # 기대값 순서 변경
                            log_tx_event(
                                frame_seq=frame_seq_to_send, attempt_num=query_attempts, event_type='PERMIT_ACK_INVALID',
                                ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                            )
                    except struct.error:
                        logger.warning(f"  Permit ACK 언패킹 실패: {permit_ack_bytes!r}")
                        log_tx_event(
                            frame_seq=frame_seq_to_send, attempt_num=query_attempts, event_type='PERMIT_ACK_UNPACK_FAIL',
                            ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                        )
                else:
                    logger.warning(f"  Permit ACK 타임아웃 또는 데이터 부족 ({len(permit_ack_bytes)}B)")
                    log_tx_event(
                        frame_seq=frame_seq_to_send, attempt_num=query_attempts, event_type='PERMIT_ACK_TIMEOUT',
                        ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                    )
                
                if not permission_received and query_attempts == RETRY_QUERY_PERMIT:
                     log_tx_event(
                        frame_seq=frame_seq_to_send, attempt_num=query_attempts, event_type='PERMIT_FINAL_FAIL',
                        ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end,
                        total_attempts_final=query_attempts, ack_received_final=False
                    )
                elif not permission_received:
                    logger.info(f"  Permit ACK 미수신/오류. 잠시 후 Query 재시도...")
                    time.sleep(1)
            
            if not permission_received:
                logger.error(f"[메시지 {msg_idx}] Frame_SEQ={frame_seq_to_send} 최종 Permit 미수신. 메시지 건너뜀.")
                continue

            # --- 2. 실제 데이터 전송 및 데이터 ACK 대기 단계 ---
            data_tx_attempts = 0
            data_ack_received = False
            ts_data_sent_latest = None

            while not data_ack_received and data_tx_attempts < RETRY_DATA_ACK:
                data_tx_attempts += 1
                logger.info(f"[메시지 {msg_idx}] Frame_SEQ={frame_seq_to_send} 데이터 패킷 전송 (시도 {data_tx_attempts}/{RETRY_DATA_ACK})")
                
                data_sent_ok, ts_data_sent_latest = _tx_data_packet(s, raw_data_packet)
                
                log_tx_event(
                    frame_seq=frame_seq_to_send,
                    attempt_num=data_tx_attempts,
                    event_type='DATA_SENT' if data_sent_ok else 'DATA_TX_FAIL',
                    ts_sent=ts_data_sent_latest
                )

                if not data_sent_ok:
                    logger.error(f"  Frame_SEQ={frame_seq_to_send} 데이터 패킷 전송 실패.")
                    if data_tx_attempts < RETRY_DATA_ACK:
                        logger.info(f"  짧은 지연 후 데이터 재전송 시도...")
                        time.sleep(0.5)
                        continue
                    else:
                        logger.error(f"  Frame_SEQ={frame_seq_to_send} 데이터 최종 전송 실패. 메시지 건너뜀.")
                        log_tx_event(
                            frame_seq=frame_seq_to_send, attempt_num=data_tx_attempts, event_type='DATA_FINAL_FAIL',
                            total_attempts_final=data_tx_attempts, ack_received_final=False
                        )
                        break
                
                logger.info(f"  Frame_SEQ={frame_seq_to_send} 데이터 ACK 대기 중 (Timeout: {s.timeout}s)...")
                data_ack_bytes = s.read(ACK_PACKET_LEN)
                ts_data_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

                if len(data_ack_bytes) == ACK_PACKET_LEN:
                    try:
                        # Data ACK 수신 시 언패킹 순서 변경: ack_type, ack_seq
                        ack_type, ack_seq = struct.unpack("!BB", data_ack_bytes)
                        # 조건문 순서 변경: 타입 먼저, 시퀀스 나중
                        if ack_type == ACK_TYPE_DATA and ack_seq == frame_seq_to_send:
                            logger.info(f"  Frame_SEQ={frame_seq_to_send} 데이터 ACK 확인 성공 (TYPE=0x{ack_type:02x}, SEQ={ack_seq})") # 로그 추가
                            data_ack_received = True
                            log_tx_event(
                                frame_seq=frame_seq_to_send, attempt_num=data_tx_attempts, event_type='DATA_ACK_OK',
                                ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end,
                                total_attempts_final=data_tx_attempts, ack_received_final=True
                            )
                        else:
                            logger.warning(f"  잘못된 데이터 ACK 수신: 기대(TYPE=0x{ACK_TYPE_DATA:02x}, SEQ={frame_seq_to_send}), 실제(TYPE=0x{ack_type:02x}, SEQ={ack_seq})") # 기대값 순서 변경
                            log_tx_event(
                                frame_seq=frame_seq_to_send, attempt_num=data_tx_attempts, event_type='DATA_ACK_INVALID',
                                ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                            )
                    except struct.error:
                        logger.warning(f"  데이터 ACK 언패킹 실패: {data_ack_bytes!r}")
                        log_tx_event(
                            frame_seq=frame_seq_to_send, attempt_num=data_tx_attempts, event_type='DATA_ACK_UNPACK_FAIL',
                            ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                        )
                else:
                    logger.warning(f"  데이터 ACK 타임아웃 또는 데이터 부족 ({len(data_ack_bytes)}B)")
                    log_tx_event(
                        frame_seq=frame_seq_to_send, attempt_num=data_tx_attempts, event_type='DATA_ACK_TIMEOUT',
                        ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                    )

                if not data_ack_received and data_tx_attempts == RETRY_DATA_ACK:
                    log_tx_event(
                        frame_seq=frame_seq_to_send, attempt_num=data_tx_attempts, event_type='DATA_ACK_FINAL_FAIL',
                        ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end,
                        total_attempts_final=data_tx_attempts, ack_received_final=False
                    )
                elif not data_ack_received:
                    logger.info(f"  데이터 ACK 미수신/오류. 잠시 후 데이터 재전송 시도...")
                    time.sleep(1)

            if not data_ack_received:
                logger.error(f"[메시지 {msg_idx}] Frame_SEQ={frame_seq_to_send} 최종 데이터 ACK 미수신. 메시지 실패 처리.")
                continue
            
            ok_count += 1
            logger.info(f"[메시지 {msg_idx}] 전송 완료 ({msg_idx}/{n})")
            print_separator(f"메시지 {msg_idx}/{n} 완료")
            time.sleep(1)

    print_separator(f"전체 전송 완료: {ok_count}/{n} 성공")
    if s and s.is_open:
        s.close()
    return ok_count

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.DEBUG) # DEBUG로 레벨 변경
    send_data(3)