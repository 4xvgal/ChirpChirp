# sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time
import logging
import serial
import struct
import datetime
from typing import Any, Dict, List, Optional, Tuple
import binascii  # 16진수 변환을 위해 추가

try:
    # source.transmitter 폴더 내의 다른 모듈을 상대 경로로 임포트
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

# 상수 정의
HANDSHAKE_TIMEOUT  = 5.0
SEND_COUNT         = 10
RETRY_HANDSHAKE    = 3
SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
ACK_PACKET_LEN     = 3

# 로거 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

current_message_pkt_id = 0

def print_separator(title: str, length: int = 60, char: str = '-') -> None:
    """
    로그 상에서 구분선을 그리며 제목을 가운데 정렬해 표시합니다.
    """
    if len(title) + 2 > length:
        logger.info(f"-- {title} --")
    else:
        pad = (length - len(title) - 2) // 2
        line = char * pad + f" {title} " + char * pad
        if len(line) < length:
            line += char
        logger.info(line)


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


def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
    """
    바이트 데이터를 보기 좋은 16진수 문자열로 변환 (여러 줄).
    """
    if not data_bytes:
        return "<empty>"
    hex_str = binascii.hexlify(data_bytes).decode('ascii')
    lines: List[str] = []
    for i in range(0, len(hex_str), bytes_per_line * 2):
        chunk = hex_str[i:i + bytes_per_line * 2]
        spaced = ' '.join(chunk[j:j+2] for j in range(0, len(chunk), 2))
        lines.append(spaced)
    return "\n  ".join(lines)


def _tx(s: serial.Serial, buf: bytes) -> Tuple[bool, Optional[datetime.datetime]]:
    ts_sent = None
    try:
        ts_sent = datetime.datetime.now(datetime.timezone.utc)
        written = s.write(buf)
        s.flush()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"TX ({len(buf)}B):\n  {bytes_to_hex_pretty_str(buf)}")
        else:
            logger.info(f"TX ({len(buf)}B)")
        return written == len(buf), ts_sent
    except Exception as e:
        logger.error(f"TX 실패: {e}")
        return False, ts_sent


def _handshake(s: serial.Serial) -> bool:
    """
    SYN/ACK 핸드셰이크 수행
    """
    print_separator("핸드셰이크 시작")
    for attempt in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"[핸드셰이크] SYN 전송 ({attempt}/{RETRY_HANDSHAKE})")
        sent_ok, _ = _tx(s, SYN_MSG)
        if not sent_ok:
            logger.warning("[핸드셰이크] SYN 전송 실패, 재시도 대기 1초")
            time.sleep(1)
            continue

        logger.info("[핸드셰이크] ACK 대기 중...")
        s.timeout = HANDSHAKE_TIMEOUT
        ack_bytes = s.read(ACK_PACKET_LEN)
        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                pid, seq, atype = struct.unpack("!BBB", ack_bytes)
                logger.info(f"[핸드셰이크] ACK 수신: PKT_ID={pid}, SEQ={seq}, TYPE=0x{atype:02x}")
                if pid == 0 and seq == 0 and atype == ACK_TYPE_HANDSHAKE:
                    logger.info("[핸드셰이크] 성공")
                    print_separator("핸드셰이크 완료")
                    return True
                else:
                    logger.warning("[핸드셰이크] 잘못된 ACK 내용")
            except struct.error:
                logger.warning(f"[핸드셰이크] ACK 언패킹 실패: {ack_bytes!r}")
        else:
            logger.warning(f"[핸드셰이크] ACK 타임아웃 또는 데이터 부족 ({len(ack_bytes)}B)")
            if ack_bytes and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"  수신 바이트:\n  {bytes_to_hex_pretty_str(ack_bytes)}")
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

    s.timeout = 1.5
    s.inter_byte_timeout = 0.1

    try:
        sr = SensorReader()
    except Exception as e:
        logger.critical(f"SensorReader 초기화 실패: {e}")
        s.close()
        return 0

    ok_count = 0
    print_separator(f"총 {n}회 데이터 전송 시작")

    for idx in range(1, n + 1):
        pkt_id = _get_next_pkt_id()
        print_separator(f"메시지 {idx}/{n} 시작")
        sample = sr.get_sensor_data()

        if not sample or 'ts' not in sample:
            logger.warning(f"[메시지 {idx}] 샘플 데이터 유효성 검사 실패, 건너뜀")
            continue

        frames = make_frames(sample, pkt_id)
        if not frames:
            logger.warning(f"[메시지 {idx}] 프레임 생성 실패, 건너뜀")
            continue

        for frame_seq, content in enumerate(frames, start=1):
            raw = bytes([len(content)]) + content
            attempts = 0
            while True:
                attempts += 1
                logger.info(f"[메시지 {idx}] PktID={pkt_id} Seq={content[0]} 전송 시도 {attempts}")
                ok, ts_sent = _tx(s, raw)
                if not ok:
                    logger.warning("  TX 오류, 재시도 0.2초 후")
                    time.sleep(0.2)
                    continue
                log_tx_event(
                    pkt_id=pkt_id,
                    frame_seq=content[0],
                    attempt_num=attempts,
                    event_type='SENT',
                    ts_sent=ts_sent
                )

                ack = s.read(ACK_PACKET_LEN)
                ts_ack = datetime.datetime.now(datetime.timezone.utc)
                if len(ack) == ACK_PACKET_LEN:
                    pid, seq, atype = struct.unpack("!BBB", ack)
                    if pid == pkt_id and seq == content[0] and atype == ACK_TYPE_DATA:
                        logger.info(f"[메시지 {idx}] ACK 확인 성공")
                        log_tx_event(
                            pkt_id=pkt_id,
                            frame_seq=content[0],
                            attempt_num=attempts,
                            event_type='ACK_OK',
                            ts_sent=ts_sent,
                            ts_ack_interaction_end=ts_ack,
                            total_attempts_final=attempts,
                            ack_received_final=True
                        )
                        break
                logger.warning("  ACK 실패 또는 잘못된 응답, 재시도 0.2초 후")
                time.sleep(0.2)

        ok_count += 1
        logger.info(f"[메시지 {idx}] 전송 완료 ({idx}/{n})")
        print_separator(f"메시지 {idx}/{n} 완료")
        time.sleep(1)

    print_separator(f"전체 전송 완료: {ok_count}/{n} 성공")
    s.close()
    return ok_count

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.DEBUG)
    send_data(10)
