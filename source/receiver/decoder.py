# -*- coding: utf-8 -*-
"""
zlib‑압축, struct 30 B 포맷 해제 → dict 복원
포맷·스케일 값은 encoder.py 와 반드시 동일해야 함
"""
from __future__ import annotations
import struct, zlib
from typing import Dict, Any, Optional

_FMT     = "<Ihhhhhhhhhff"
_SCALES  = (1, 1000, 1000, 1000, 10, 10, 10, 10, 10, 10, 1.0, 1.0)

def decompress_data(buf: bytes) -> Optional[Dict[str, Any]]:
    try:
        unpacked = struct.unpack(_FMT, zlib.decompress(buf))
        ts, ax, ay, az, gx, gy, gz, roll, pitch, yaw, lat, lon = (
            u / s for u, s in zip(unpacked, _SCALES)
        )
        return {
            "ts": ts,
            "accel": {"ax": ax, "ay": ay, "az": az},
            "gyro":  {"gx": gx, "gy": gy, "gz": gz},
            "angle": {"roll": roll, "pitch": pitch, "yaw": yaw},
            "gps":   {"lat": lat, "lon": lon},
        }
    except Exception as e:
        print(f"[decoder] 복원 실패: {e}")
        return None
