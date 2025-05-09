# -*- coding: utf-8 -*-

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
    try:
        written = s.write(buf)
        s.flush()
        return written == len(buf)
    except Exception as e:
        logging.error(f"TX 실패: {e}")
        return False


def _handshake(s: serial.Serial) -> bool:
    for i in range(RETRY_HANDSHAKE):
        s.write(SYN); s.flush()
        start = time.time()
        while time.time() - start < HANDSHAKE_TIMEOUT:
            if s.readline() == ACK:
                logging.info("핸드셰이크 성공")
                return True
        logging.warning(f"핸드셰이크 재시도 {i+1}/{RETRY_HANDSHAKE}")
    return False


def send_data(n: int = SEND_COUNT) -> int:

    s = _open_serial()
    if not _handshake(s):
        logging.error("핸드셰이크 최종 실패, 종료.")
        s.close()
        return 0

    s.timeout = 0.1
    sr = SensorReader()
    ok = 0

    for i in range(1, n + 1):
        sample = sr.get_sensor_data()
        frames = make_frames(sample)
        if not frames:
            logging.warning(f"[{i}/{n}] 불완전 샘플, 건너뜀")
            time.sleep(1)
            continue

        success = True
        for j, f in enumerate(frames, 1):
            if len(f) > FRAME_MAX:
                logging.error(f"[{i}] 프레임 크기 초과: {len(f)}")
                success = False
                break
            pkt = bytes([len(f)]) + f
            if not _tx(s, pkt):
                logging.error(f"[{i}] 프레임 {j}/{len(frames)} 전송 실패")
                success = False
                break
            time.sleep(DELAY_BETWEEN)

        if success:
            ok += 1
            logging.info(f"[{i}/{n}] 전송 성공")
        else:
            logging.error(f"[{i}/{n}] 전송 실패")

        time.sleep(1)

    logging.info(f"총 {n}회 중 {ok}회 성공")
    s.close()
    return ok


if __name__ == "__main__":
    send_data()
