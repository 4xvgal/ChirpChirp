# -*- coding: utf-8 -*-
"""
sender.py – LoRa 송신 (LEN‑SEQ‑TOTAL‑PAYLOAD)
"""
from __future__ import annotations
import time, logging, serial
from typing import Dict, Any, List

from e22_config    import init_serial
from packetizer    import make_frames          # 2B 헤더+payload 리스트 생성
from sensor_reader import SensorReader

MAX_PAYLOAD      = 56          # packetizer와 동일
FRAME_MAX        = 2 + MAX_PAYLOAD           # 헤더+payload
LEN_MAX          = FRAME_MAX                 # LEN 값
HANDSHAKE_TIMEOUT = 2.0

SYN, ACK = b"SYN\r\n", b"ACK\n"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

def _open() -> serial.Serial:
    s = init_serial()
    s.timeout = HANDSHAKE_TIMEOUT
    time.sleep(0.1)
    return s

def _tx(s: serial.Serial, buf: bytes) -> bool:
    try:
        s.write(buf); s.flush(); return True
    except Exception as e:
        logging.error(f"TX 실패: {e}"); return False

def _handshake(s: serial.Serial) -> bool:
    return _tx(s, SYN) and s.readline() == ACK

# ────────── 전송 루틴 ──────────
def send_sample(sample: Dict[str, Any]) -> bool:
    s = _open()
    try:
        if not _handshake(s):
            logging.error("핸드셰이크 실패"); return False

        frames: List[bytes] = make_frames(sample)   # 2B 헤더+payload
        for f in frames:
            if len(f) > FRAME_MAX:
                raise ValueError("프레임 길이 초과")
            pkt = bytes([len(f)]) + f               # LEN + 본문
            if not _tx(s, pkt):
                return False
            time.sleep(0.3)                         # LoRa 채널 보호
        return True
    finally:
        s.close()

def send_data(n: int = 1000) -> int:               # 기본 1000회
    sr, ok = SensorReader(), 0
    for i in range(1, n + 1):
        if send_sample(sr.get_sensor_data()):
            ok += 1
        time.sleep(1)
    logging.info(f"{n}회 중 {ok}회 성공")
    return ok

if __name__ == "__main__":
    send_data()         # 1 000회 실행
