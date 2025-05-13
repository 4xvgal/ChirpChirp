# packetizer.py
# -*- coding: utf-8 -*-
"""
packetizer.py
• encoder.compress_data() 결과를 LoRa 프레임(PKT_ID+SEQ+TOTAL+PAYLOAD)으로 변환.
"""
from __future__ import annotations
from typing import Dict, Any, List
# encoder에서 MAX_PAYLOAD_CHUNK를 임포트 (split_into_packets의 기본값으로 사용됨)
from encoder import compress_data, split_into_packets, MAX_PAYLOAD_CHUNK

# PKT_ID, SEQ, TOTAL 헤더의 크기
PACKET_HEADER_SIZE = 3 # PKT_ID(1) + SEQ(1) + TOTAL(1)

def make_frames(sample: Dict[str, Any], pkt_id: int) -> List[bytes]:
    """
    센서 dict -> zlib 압축 -> 각 프레임을 다음 바이트 시퀀스로 변환:
    [ PKT_ID (1B) | SEQ (1B) | TOTAL (1B) | PAYLOAD_CHUNK ]
    """
    if not (0 <= pkt_id <= 255):
        raise ValueError("PKT_ID must be between 0 and 255.")

    blob = compress_data(sample)
    if not blob: # 압축 결과가 비어있으면 빈 프레임 리스트 반환
        return []

    # split_into_packets는 PAYLOAD_CHUNK만을 위한 max_size를 사용함
    pkts_info = split_into_packets(blob, MAX_PAYLOAD_CHUNK)

    frames = []
    for p_info in pkts_info:
        if p_info["total"] == 0 and not p_info["payload"]: # encoder가 빈 데이터에 대해 반환한 경우
            continue # 빈 프레임은 만들지 않음

        seq = p_info["seq"]
        total = p_info["total"]
        payload_chunk = p_info["payload"]

        # 프레임: PKT_ID(1B) + SEQ(1B) + TOTAL(1B) + PAYLOAD_CHUNK
        frame = bytes([pkt_id, seq, total]) + payload_chunk
        frames.append(frame)
    
    return frames