# -*- coding: utf-8 -*-
"""
encoder.py
• 센서 dict  →  struct 바이너리 → zlib 압축
• 압축 블록을 LoRa 최대 56 B로 쪼개는 split_into_packets()
"""
from __future__ import annotations
import struct, zlib, math
from typing import Dict, Any, List

# ────────── 직렬화 ──────────
_FMT = "<Ihhhhhhhhhff"            # 4+18+8 = 30 B → zlib 압축 후 18~22 B
_FIELDS = (
    ("ts",        1),             # uint32  (s)
    ("accel.ax",  1000),          # int16   (0.001 g)
    ("accel.ay",  1000),
    ("accel.az",  1000),
    ("gyro.gx",   10),            # int16   (0.1 °/s)
    ("gyro.gy",   10),
    ("gyro.gz",   10),
    ("angle.roll", 10),           # int16   (0.1 °)
    ("angle.pitch",10),
    ("angle.yaw", 10),
    ("gps.lat",   1.0),           # float32 (°)
    ("gps.lon",   1.0),
)

def _extract(src: Dict[str, Any], dotted: str):
    """``"gyro.gx"`` 같은 경로를 따라 값 추출"""
    parts = dotted.split('.')
    v = src
    for p in parts:
        v = v[p]
    return v

def compress_data(data: Dict[str, Any]) -> bytes:
    """센서 dict → struct(30 B) → zlib(level 9)"""
    packed = struct.pack(
        _FMT,
        int(data["ts"]),
        *[int(_extract(data, k) * scale) if isinstance(scale, int)
          else float(_extract(data, k)) for k, scale in _FIELDS[1:]]
    )
    return zlib.compress(packed, level=9)

# ────────── 패킷화 ──────────
MAX_PAYLOAD = 56                  # 58(LoRa) - 2(헤더)

def split_into_packets(data: bytes, max_size: int = MAX_PAYLOAD) -> List[Dict]:
    if max_size <= 0:
        raise ValueError("max_size must be > 0")
    total = math.ceil(len(data) / max_size)
    return [
        {"seq": i + 1, "total": total, "payload": data[i*max_size:(i+1)*max_size]}
        for i in range(total)
    ]
