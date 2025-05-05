# -*- coding: utf-8 -*-
from __future__ import annotations

import time, logging, serial, json, base64
from typing import Any, Dict

from e22_config import init_serial
from packetizer import split_into_packets
from encoder import compress_data
from sensor_reader import SensorReader

HEADER_SIZE = 2
LORA_FRAME_LIMIT = 58
JSON_OVERHEAD   = 29
RAW_MAX_PER_PKT = 5     # 47B JSON (안전)
MAX_RETRY = 3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

_open = lambda: (time.sleep(0.1), init_serial())[1]
_close = lambda s: s.is_open and s.close() if s else None


def _tx(s: serial.Serial, buf: bytes) -> bool:
    for _ in range(MAX_RETRY):
        try:
            if s.write(buf) == len(buf):
                s.flush(); return True
        except serial.SerialException as e:
            logging.error(e)
        time.sleep(0.3)
    return False


def _send_once(data: Dict[str, Any]) -> bool:
    pkts = split_into_packets(compress_data(data), RAW_MAX_PER_PKT)
    s = _open(); tot = len(pkts)
    try:
        for p in pkts:
            line = json.dumps({"seq": p["seq"], "total": tot,
                              "payload": base64.b64encode(p["payload"]).decode()}) + "\r\n"
            if len(line) > LORA_FRAME_LIMIT or not _tx(s, line.encode()):
                return False
            time.sleep(0.3)
        return True
    finally:
        _close(s)


def send_data(n: int = 100) -> int:
    r, ok = SensorReader(), 0
    for i in range(1, n + 1):
        logging.info(f"{i}/{n}")
        if _send_once(r.get_sensor_data()):
            ok += 1
        else:
            break
        time.sleep(1)
    return ok

if __name__ == "__main__":
    logging.info(f"success {send_data()}")
