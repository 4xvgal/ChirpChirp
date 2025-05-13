# encoder.py
# -*- coding: utf-8 -*-
"""
encoder.py
• 센서 dict  →  struct 바이너리 → zlib 압축
• 압축 블록을 LoRa 최대 크기에 맞춰 쪼개는 split_into_packets()
"""
from __future__ import annotations
import struct, zlib, math
from typing import Dict, Any, List


_FMT = "<Ihhhhhhhhhff"
_FIELDS = (
    ("ts",        1),
    ("accel.ax",  1000),
    ("accel.ay",  1000),
    ("accel.az",  1000),
    ("gyro.gx",   10),
    ("gyro.gy",   10),
    ("gyro.gz",   10),
    ("angle.roll", 10),
    ("angle.pitch",10),
    ("angle.yaw", 10),
    ("gps.lat",   1.0),
    ("gps.lon",   1.0),
)

def _extract(src: Dict[str, Any], dotted: str):
    parts = dotted.split('.')
    v = src
    for p in parts:
        v = v[p]
    return v

def compress_data(data: Dict[str, Any]) -> bytes:
    packed = struct.pack(
        _FMT,
        int(data["ts"]),
        *[int(_extract(data, k) * scale) if isinstance(scale, int)
          else float(_extract(data, k)) for k, scale in _FIELDS[1:]]
    )
    return zlib.compress(packed, level=9)

# ────────── 패킷화 ──────────
# LoRa 최대 프레임 크기가 58바이트라고 가정.
# 데이터 패킷 헤더: LEN(1)은 sender가 붙임.
# 우리가 packetizer에서 만드는 부분: PKT_ID(1) + SEQ(1) + TOTAL(1) = 3 바이트
# 따라서, 순수 PAYLOAD_CHUNK가 가질 수 있는 최대 크기는
# 58 (LoRa 최대) - 1 (LEN) - 3 (PKT_ID+SEQ+TOTAL) = 54 바이트
# MAX_PAYLOAD_CHUNK = 54 (기존 MAX_PAYLOAD = 56에서 변경)
MAX_PAYLOAD_CHUNK = 54

def split_into_packets(data: bytes, max_payload_chunk_size: int = MAX_PAYLOAD_CHUNK) -> List[Dict]:
    if max_payload_chunk_size <= 0:
        raise ValueError("max_payload_chunk_size must be > 0")
    
    num_frames = math.ceil(len(data) / max_payload_chunk_size)
    if num_frames == 0 and len(data) > 0: # 데이터가 있지만 chunk_size가 너무 커서 num_frames가 0이 되는 경우 방지
        num_frames = 1
    elif len(data) == 0: # 데이터가 없는 경우
        return [{"seq": 0, "total": 0, "payload": b""}] # total 0인 빈 패킷 하나 반환 또는 빈 리스트

    return [
        {"seq": i, "total": num_frames, "payload": data[i*max_payload_chunk_size:(i+1)*max_payload_chunk_size]}
        for i in range(num_frames)
    ]