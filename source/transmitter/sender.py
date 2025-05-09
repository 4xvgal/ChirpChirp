# -*- coding: utf-8 -*-
"""
sender.py – LoRa 송신기
· packetizer.make_frames() 로 받은 바이트를 실제로 전송
"""
from __future__ import annotations
import time, logging, serial
from typing import Any, Dict, List

from e22_config   import init_serial
from packetizer   import make_frames
from sensor_reader import SensorReader

# ────────── 설정 ──────────
LORA_FRAME_LIMIT  = 58
MAX_RETRY         = 3
HANDSHAKE_TIMEOUT = 2.0
SYN_MSG, ACK_MSG  = b"SYN\r\n", "ACK"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# ────────── 시리얼 유틸 ──────────
def _open_serial() -> serial.Serial:
    s = init_serial()
    s.timeout = HANDSHAKE_TIMEOUT
    time.sleep(0.1)
    return s

def _tx(s: serial.Serial, buf: bytes) -> bool:
    for _ in range(MAX_RETRY):
        try:
            if s.write(buf) == len(buf):
                s.flush(); return True
        except Exception as e:
            logging.warning(f"TX 재시도: {e}")
        time.sleep(0.3)
    return False

def _handshake(s: serial.Serial) -> bool:
    if not _tx(s, SYN_MSG):
        return False
    return s.readline().decode(errors="ignore").strip() == ACK_MSG

# ────────── 전송 루틴 ──────────
def send_sample(sample: Dict[str, Any]) -> bool:
    s = _open_serial()
    try:
        if not _handshake(s):
            logging.error("핸드셰이크 실패"); return False

        frames: List[bytes] = make_frames(sample)
        s.timeout = 0.1
        for i, f in enumerate(frames, 1):
            if len(f) > LORA_FRAME_LIMIT:
                raise ValueError("프레임 길이 초과")
            if not _tx(s, f):
                logging.error(f"{i}/{len(frames)} 전송 실패"); return False
            time.sleep(0.3)
        logging.info(f"✓ {len(frames)}개 프레임 전송 완료")
        return True
    finally:
        s.close()

def send_data(n: int = 100) -> int:
    r, ok = SensorReader(), 0
    for i in range(1, n + 1):
        if send_sample(r.get_sensor_data()):
            ok += 1
        time.sleep(1)
    logging.info(f"{n}회 중 {ok}회 성공")
    return ok

if __name__ == "__main__":
    send_data(5)     # 짧게 테스트
