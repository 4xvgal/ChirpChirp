# -*- coding: utf-8 -*-
"""
sender.py — LoRa 트랜스미터 (LEN-SEQ-TOTAL-PAYLOAD)  
· compress_data() 실패 시 건너뜀  
· 1 000회 전송, 핸드셰이크 5회 재시도
"""
from __future__ import annotations
import time, logging, serial
from typing import Any, Dict, List

from e22_config    import init_serial
from packetizer    import make_frames
from sensor_reader import SensorReader

# ────────── 설정 ──────────
MAX_PAYLOAD       = 56
FRAME_MAX         = 2 + MAX_PAYLOAD
HANDSHAKE_TIMEOUT = 2.0
SEND_COUNT        = 1000
RETRY_HANDSHAKE   = 5
RETRY_TX          = 3
DELAY_BETWEEN     = 0.3

SYN = b"SYN\r\n"
ACK = b"ACK\r\n"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def _open_serial() -> serial.Serial:
    s = init_serial()
    s.timeout = HANDSHAKE_TIMEOUT
    time.sleep(0.1)
    return s


def _tx(s: serial.Serial, buf: bytes) -> bool:
    for _ in range(RETRY_TX):
        try:
            if s.write(buf) == len(buf):
                s.flush()
                return True
        except Exception as e:
            logging.warning(f"TX 재시도: {e}")
        time.sleep(DELAY_BETWEEN)
    return False


def _handshake(s: serial.Serial) -> bool:
    # SYN→ACK 5회 재시도
    for attempt in range(RETRY_HANDSHAKE):
        s.write(SYN); s.flush()
        start = time.time()
        while time.time() - start < HANDSHAKE_TIMEOUT:
            resp = s.readline()
            if resp == ACK:
                return True
        logging.warning(f"핸드셰이크 재시도 {attempt+1}/{RETRY_HANDSHAKE}")
    return False


def send_sample(sample: Dict[str, Any]) -> bool:
    # 1) 직렬화 방어
    frames = make_frames(sample)
    if not frames:
        return False

    # 2) 포트 열고 핸드셰이크
    s = _open_serial()
    try:
        if not _handshake(s):
            logging.error("핸드셰이크 실패")
            return False

        # 3) 프레임 전송 (LEN + [seq,total] + payload)
        s.timeout = 0.1
        for i, f in enumerate(frames, 1):
            if len(f) > FRAME_MAX:
                raise ValueError("프레임 길이 초과")
            pkt = bytes([len(f)]) + f
            if not _tx(s, pkt):
                logging.error(f"{i}/{len(frames)} 전송 실패")
                return False
            time.sleep(DELAY_BETWEEN)
        logging.info(f"✓ {len(frames)}개 프레임 전송 완료")
        return True

    finally:
        s.close()


def send_data(n: int = SEND_COUNT) -> int:
    sr, ok = SensorReader(), 0
    for i in range(1, n + 1):
        if send_sample(sr.get_sensor_data()):
            ok += 1
        time.sleep(1)
    logging.info(f"{n}회 중 {ok}회 성공")
    return ok


if __name__ == "__main__":
    send_data()
