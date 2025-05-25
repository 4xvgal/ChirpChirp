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
    from .packetizer    import make_frames
    from .sensor_reader import SensorReader
    from .tx_logger     import log_tx_event
except ImportError:
    try:
        from e22_config    import init_serial
        from packetizer    import make_frames
        from sensor_reader import SensorReader
        from tx_logger     import log_tx_event
    except ImportError as e:
        print(f"모듈 임포트 실패: {e}. 프로젝트 구조 및 PYTHONPATH를 확인하세요.")
        exit(1)

# --- 상수 정의 ---
GENERIC_TIMEOUT    = 65.0
SEND_COUNT         = 100
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
    level=logging.INFO, # 필요시 logging.DEBUG로 변경
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
        # --- 3단계 로깅 추가 ---
        if buf: # buf가 비어있지 않은 경우에만 첫 바이트 로깅
            logger.debug(f"DATA PKT TX 직전 LENGTH 바이트 값 (buf[0]): 0x{buf[0]:02x} ({buf[0]})")
        # --- 로깅 추가 끝 ---
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

def _tx_control_packet(s: serial.Serial, seq: int, packet_type: int) -> bool:
    pkt_bytes = struct.pack("!BB", packet_type, seq)
    try:
        written = s.write(pkt_bytes)
        s.flush()
        type_name = {
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

def _handshake(s: serial.Serial) -> bool:
    print_separator("핸드셰이크 시작")
    s.timeout = GENERIC_TIMEOUT
    for attempt in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"[핸드셰이크] SYN 전송 ({attempt}/{RETRY_HANDSHAKE})")
        sent_ok, ts_syn_sent = _tx_data_packet(s, SYN_MSG) # SYN_MSG는 길이가 고정적이므로 첫 바이트 로깅은 큰 의미 없을 수 있음

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
        ack_bytes = s.read(ACK_PACKET_LEN)
        ts_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                atype, seq = struct.unpack("!BB", ack_bytes)
                logger.info(f"[핸드셰이크] ACK 수신: TYPE=0x{atype:02x}, SEQ={seq}")
                if atype == ACK_TYPE_HANDSHAKE and seq == HANDSHAKE_ACK_SEQ:
                    logger.info("[핸드셰이크] 성공")
                    print_separator("핸드셰이크 완료")
                    log_tx_event(
                        frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt,
                        event_type='HANDSHAKE_ACK_OK',
                        ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end,
                        total_attempts_final=attempt, ack_received_final=True
                    )
                    return True
                else:
                    logger.warning(f"[핸드셰이크] 잘못된 ACK 내용 (기대: TYPE=0x{ACK_TYPE_HANDSHAKE:02x}, SEQ={HANDSHAKE_ACK_SEQ})")
                    log_tx_event(
                        frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_INVALID',
                        ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end
                    )
            except struct.error:
                logger.warning(f"[핸드셰이크] ACK 언패킹 실패: {ack_bytes!r}")
                log_tx_event(
                    frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_UNPACK_FAIL',
                    ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end
                )
        else:
            logger.warning(f"[핸드셰이크] ACK 타임아웃 또는 데이터 부족 ({len(ack_bytes)}B)")
            if ack_bytes and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"  수신 바이트:\n  {bytes_to_hex_pretty_str(ack_bytes)}")
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_TIMEOUT',
                ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end
            )

        if attempt < RETRY_HANDSHAKE:
             time.sleep(1)
        elif attempt == RETRY_HANDSHAKE:
            log_tx_event(
                frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_FINAL_FAIL',
                ts_sent=ts_syn_sent,
                ts_ack_interaction_end=ts_ack_interaction_end,
                total_attempts_final=attempt, ack_received_final=False
            )

    logger.error("[핸드셰이크] 최종 실패")
    print_separator("핸드셰이크 실패")
    return False

def send_data(n: int = SEND_COUNT, mode: str = "reliable") -> int:
    try:
        s = _open_serial()
    except Exception:
        return -1

    if not _handshake(s):
        s.close()
        return 0

    s.timeout = GENERIC_TIMEOUT
    s.inter_byte_timeout = 0.1

    effective_retry_query_permit: int
    effective_retry_data_ack: int

    if mode == "PDR":
        effective_retry_query_permit = 1
        effective_retry_data_ack = 1
        logger.info("PDR 측정 모드로 실행됩니다. 재전송 비활성화.")
    elif mode == "reliable":
        effective_retry_query_permit = RETRY_QUERY_PERMIT
        effective_retry_data_ack = RETRY_DATA_ACK
        logger.info("신뢰성 전송 모드로 실행됩니다. 재전송 활성화.")
    else:
        logger.error(f"알 수 없는 모드: {mode}. 'reliable' 또는 'PDR'을 사용하세요.")
        if s and s.is_open:
            s.close()
        return -2

    try:
        sr = SensorReader()
    except Exception as e:
        logger.critical(f"SensorReader 초기화 실패: {e}")
        s.close()
        return -3

    reliable_ok_count = 0
    pdr_data_acks_received_count = 0
    pdr_messages_tx_initiated_count = 0

    current_message_seq_counter = 0
    print_separator(f"총 {n}회 데이터 전송 시작 (모드: {mode})")

    for msg_idx in range(1, n + 1):
        print_separator(f"메시지 {msg_idx}/{n} (Message SEQ: {current_message_seq_counter}) 시작")
        sample = sr.get_sensor_data()

        if not sample or 'ts' not in sample: # sensor_reader가 None을 반환하거나 ts가 없는 경우
            logger.warning(f"[메시지 {msg_idx}] 유효하지 않은 샘플 데이터 수신, 건너뜀. Sample: {sample}")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue

        frames = make_frames(sample, current_message_seq_counter)
        if not frames:
            logger.warning(f"[메시지 {msg_idx}] 프레임 생성 실패 (아마도 compress_data 실패), 건너뜀")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue

        # --- 3단계 로깅 추가 ---
        # frames[0]은 [MESSAGE_SEQ (1B) | PAYLOAD_CHUNK] 형태. 이 길이가 데이터 패킷의 첫 바이트인 LENGTH가 됨.
        frame_content_len = len(frames[0])
        logger.info(f"[메시지 {msg_idx}] 생성된 프레임 내용 길이 (frame_content_len / 데이터 패킷의 LENGTH 값): {frame_content_len} (0x{frame_content_len:02x})")
        # --- 로깅 추가 끝 ---

        if mode == "PDR":
            pdr_messages_tx_initiated_count += 1

        raw_data_packet = bytes([frame_content_len]) + frames[0] # LENGTH 바이트 + 프레임 내용
        frame_seq_for_ack_handling = frames[0][0] # 프레임 내용의 첫 바이트는 MESSAGE_SEQ

        query_attempts = 0
        permission_received = False
        ts_query_sent_latest = None

        while not permission_received and query_attempts < effective_retry_query_permit:
            query_attempts += 1
            logger.info(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 질의(Query) 전송 (시도 {query_attempts}/{effective_retry_query_permit})")

            ts_query_sent_latest = datetime.datetime.now(datetime.timezone.utc)
            query_sent_ok = _tx_control_packet(s, frame_seq_for_ack_handling, QUERY_TYPE_SEND_REQUEST)

            log_tx_event(
                frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts,
                event_type='QUERY_SENT' if query_sent_ok else 'QUERY_TX_FAIL',
                ts_sent=ts_query_sent_latest
            )

            if not query_sent_ok:
                logger.error(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} Query 전송 실패.")
                if query_attempts < effective_retry_query_permit:
                    time.sleep(0.5)
                    continue
                else:
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='QUERY_FINAL_FAIL',
                        ts_sent=ts_query_sent_latest,
                        total_attempts_final=query_attempts, ack_received_final=False
                    )
                    break

            logger.info(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 허가(Permit) 대기 중 (Timeout: {s.timeout}s)...")
            # Permit ACK 수신 전 입력 버퍼 초기화 (선택적, 이전 데이터 영향 최소화)
            s.reset_input_buffer()
            permit_ack_bytes = s.read(ACK_PACKET_LEN)
            ts_permit_interaction_end = datetime.datetime.now(datetime.timezone.utc)

            if len(permit_ack_bytes) == ACK_PACKET_LEN:
                try:
                    permit_type, permit_seq = struct.unpack("!BB", permit_ack_bytes)
                    logger.debug(f"Permit ACK 후보 수신: TYPE=0x{permit_type:02x}, SEQ=0x{permit_seq:02x}") # DEBUG 로그 추가
                    if permit_type == ACK_TYPE_SEND_PERMIT and permit_seq == frame_seq_for_ack_handling:
                        permission_received = True
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_OK',
                            ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end,
                            total_attempts_final=query_attempts, ack_received_final=True
                        )
                    else:
                        logger.warning(f"Permit ACK 내용 불일치: 수신 TYPE=0x{permit_type:02x}(기대0x{ACK_TYPE_SEND_PERMIT:02x}), 수신 SEQ=0x{permit_seq:02x}(기대0x{frame_seq_for_ack_handling:02x})")
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_INVALID',
                            ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                        )
                except struct.error:
                    logger.warning(f"Permit ACK 언패킹 실패: {permit_ack_bytes!r}")
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_UNPACK_FAIL',
                        ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                    )
            else:
                logger.warning(f"Permit ACK 타임아웃 또는 데이터 부족 ({len(permit_ack_bytes)}B). 수신 데이터: {bytes_to_hex_pretty_str(permit_ack_bytes)}")
                log_tx_event(
                    frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_ACK_TIMEOUT',
                    ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end
                )

            if not permission_received:
                if query_attempts == effective_retry_query_permit:
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=query_attempts, event_type='PERMIT_FINAL_FAIL',
                        ts_sent=ts_query_sent_latest, ts_ack_interaction_end=ts_permit_interaction_end,
                        total_attempts_final=query_attempts, ack_received_final=False
                    )
                elif query_attempts < effective_retry_query_permit:
                    time.sleep(1)

        if not permission_received:
            logger.error(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 최종 Permit 미수신. 메시지 건너뜀.")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue

        data_tx_attempts = 0
        data_ack_received = False
        ts_data_sent_latest = None

        while not data_ack_received and data_tx_attempts < effective_retry_data_ack:
            data_tx_attempts += 1
            logger.info(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 패킷 전송 (시도 {data_tx_attempts}/{effective_retry_data_ack})")

            data_sent_ok, ts_data_sent_latest = _tx_data_packet(s, raw_data_packet)

            log_tx_event(
                frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts,
                event_type='DATA_SENT' if data_sent_ok else 'DATA_TX_FAIL',
                ts_sent=ts_data_sent_latest
            )

            if not data_sent_ok:
                logger.error(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 패킷 전송 실패.")
                if data_tx_attempts < effective_retry_data_ack:
                    time.sleep(0.5)
                    continue
                else:
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_FINAL_FAIL',
                        ts_sent=ts_data_sent_latest,
                        total_attempts_final=data_tx_attempts, ack_received_final=False
                    )
                    break

            logger.info(f"  MESSAGE_SEQ={frame_seq_for_ack_handling} 데이터 ACK 대기 중 (Timeout: {s.timeout}s)...")
            # Data ACK 수신 전 입력 버퍼 초기화
            s.reset_input_buffer()
            data_ack_bytes = s.read(ACK_PACKET_LEN)
            ts_data_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

            if len(data_ack_bytes) == ACK_PACKET_LEN:
                try:
                    ack_type, ack_seq = struct.unpack("!BB", data_ack_bytes)
                    logger.debug(f"Data ACK 후보 수신: TYPE=0x{ack_type:02x}, SEQ=0x{ack_seq:02x}")
                    if ack_type == ACK_TYPE_DATA and ack_seq == frame_seq_for_ack_handling:
                        data_ack_received = True
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_OK',
                            ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end,
                            total_attempts_final=data_tx_attempts, ack_received_final=True
                        )
                        if mode == "PDR":
                            pdr_data_acks_received_count += 1
                    else:
                        logger.warning(f"Data ACK 내용 불일치: 수신 TYPE=0x{ack_type:02x}(기대0x{ACK_TYPE_DATA:02x}), 수신 SEQ=0x{ack_seq:02x}(기대0x{frame_seq_for_ack_handling:02x})")
                        log_tx_event(
                            frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_INVALID',
                            ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                        )
                except struct.error:
                    logger.warning(f"Data ACK 언패킹 실패: {data_ack_bytes!r}")
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_UNPACK_FAIL',
                        ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                    )
            else:
                logger.warning(f"Data ACK 타임아웃 또는 데이터 부족 ({len(data_ack_bytes)}B). 수신 데이터: {bytes_to_hex_pretty_str(data_ack_bytes)}")
                log_tx_event(
                    frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_TIMEOUT',
                    ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end
                )

            if not data_ack_received:
                if data_tx_attempts == effective_retry_data_ack:
                    log_tx_event(
                        frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_FINAL_FAIL',
                        ts_sent=ts_data_sent_latest, ts_ack_interaction_end=ts_data_ack_interaction_end,
                        total_attempts_final=data_tx_attempts, ack_received_final=False
                    )
                elif data_tx_attempts < effective_retry_data_ack:
                    time.sleep(1)

        if data_ack_received:
            if mode == "reliable":
                reliable_ok_count += 1
            logger.info(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 전송 완료 ({msg_idx}/{n})")
            print_separator(f"메시지 {msg_idx}/{n} 완료")
        else:
            logger.error(f"[메시지 {msg_idx}] MESSAGE_SEQ={frame_seq_for_ack_handling} 최종 데이터 ACK 미수신. 메시지 실패 처리.")

        current_message_seq_counter = (current_message_seq_counter + 1) % 256
        time.sleep(1) # 다음 메시지 전송 전 지연 (LoRa 채널 사용 등 고려)

    final_return_value: int
    if mode == "PDR":
        pdr_value_over_n = (pdr_data_acks_received_count / n) if n > 0 else 0.0

        summary_msg_parts = [
            f"PDR Mode 결과 - 총 메시지 루프 반복 (n): {n}",
            f"실제 전송 시작된 메시지 (프레임 생성 성공): {pdr_messages_tx_initiated_count}",
            f"데이터 ACK 수신: {pdr_data_acks_received_count}",
            f"PDR (ACK 수신 / n): {pdr_value_over_n:.2%}"
        ]
        if pdr_messages_tx_initiated_count > 0:
            pdr_value_over_initiated = pdr_data_acks_received_count / pdr_messages_tx_initiated_count
            summary_msg_parts.append(f"PDR (ACK 수신 / 실제 전송 시작): {pdr_value_over_initiated:.2%}")

        summary_msg = ", ".join(summary_msg_parts)
        logger.info(summary_msg)
        print_separator(f"PDR 전송 완료: {pdr_data_acks_received_count}/{pdr_messages_tx_initiated_count} ACK 수신 (PDR: {pdr_value_over_initiated if pdr_messages_tx_initiated_count > 0 else 0.0:.2%})")
        final_return_value = pdr_data_acks_received_count
    else: # reliable mode
        summary_msg = f"신뢰성 전송 완료: {reliable_ok_count}/{n} 메시지 성공적 전송"
        logger.info(summary_msg)
        print_separator(summary_msg)
        final_return_value = reliable_ok_count

    if s and s.is_open:
        s.close()
    return final_return_value

if __name__ == '__main__':
    # 로깅 레벨 설정 (INFO 또는 DEBUG)
    # logging.getLogger().setLevel(logging.DEBUG) # 상세 정보 로깅
    logging.getLogger().setLevel(logging.INFO)   # 일반 정보 로깅
    # 특정 로거의 레벨만 조정할 수도 있습니다.
    # logging.getLogger('sender').setLevel(logging.DEBUG)


    # --- PDR 모드 테스트 ---
    logger.info("\n" + "="*10 + " PDR 모드 테스트 시작 " + "="*10)
    pdr_acks_received = send_data(SEND_COUNT, mode="PDR")
    logger.info(f"PDR 모드 테스트 종료, 수신된 데이터 ACK 총계: {pdr_acks_received}")
    logger.info("="*40 + "\n")

    # --- Reliable 모드 테스트 ---
    # logger.info("\n" + "="*10 + " Reliable 모드 테스트 시작 " + "="*10)
    # reliable_success_count = send_data(SEND_COUNT, mode="reliable")
    # logger.info(f"Reliable 모드 테스트 종료, 성공적으로 전송된 메시지 수: {reliable_success_count}")
    # logger.info("="*40 + "\n")
