# -*- coding: utf-8 -*-
"""
packetizer.py
• encoder.compress_data() 결과를 LoRa 프레임(≤58 B)로 변환만 담당
"""
from __future__ import annotations
from typing import Dict, Any, List
from encoder import compress_data, split_into_packets, MAX_PAYLOAD

def make_frames(sample: Dict[str, Any]) -> List[bytes]:
    """센서 dict → zlib 압축 → 2 B 헤더+payload 바이트 시퀀스 반환"""
    blob   = compress_data(sample)
    pkts   = split_into_packets(blob, MAX_PAYLOAD)
    return [bytes([p["seq"], p["total"]]) + p["payload"] for p in pkts]
