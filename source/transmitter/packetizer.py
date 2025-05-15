# packetizer.py
# -*- coding: utf-8 -*-
"""
packetizer.py
• encoder.compress_data() 결과를 LoRa 프레임(SEQ+PAYLOAD_CHUNK)으로 변환.
"""
from __future__ import annotations
import logging # logging 추가
from typing import Dict, Any, List

try:
    from .encoder import compress_data, split_into_packets, MAX_PAYLOAD_CHUNK
except ImportError: # 단독 실행 또는 PYTHONPATH 문제 시
    from encoder import compress_data, split_into_packets, MAX_PAYLOAD_CHUNK


logger = logging.getLogger(__name__) # packetizer.py의 로거

# PACKET_HEADER_SIZE 상수는 더 이상 현재 프레임 구조에 직접적으로 필요 없음
# PACKET_HEADER_SIZE = 3 

def make_frames(sample: Dict[str, Any], pkt_id: int) -> List[bytes]:
    """
    센서 dict -> zlib 압축 -> 단일 프레임을 다음 바이트 시퀀스로 변환 (리스트에 담아 반환):
    [ SEQ (1B) | PAYLOAD_CHUNK ]
    pkt_id는 프레임 자체에 포함되지 않지만, 메시지 식별에 사용됩니다.
    """
    blob = compress_data(sample)
    if not blob:
        logger.warning(f"PKT_ID {pkt_id}: compress_data 결과가 비어있어 빈 프레임 리스트 반환")
        return []

    # split_into_packets는 이제 단일 '청크' 정보를 담은 리스트를 반환
    # max_payload_chunk_size 인자는 encoder.MAX_PAYLOAD_CHUNK 기본값을 사용
    pkts_info_list = split_into_packets(blob) 

    # pkts_info_list는 항상 요소가 하나인 리스트이거나, split_into_packets가 빈 데이터를 특별 처리하면 그에 따름
    # encoder.py의 split_into_packets는 빈 데이터에 대해 [{"seq": 1, "payload": b""}]를 반환함
    if not pkts_info_list: # 이론상 발생하지 않아야 함 (split_into_packets가 항상 리스트 반환)
        logger.error(f"PKT_ID {pkt_id}: split_into_packets가 예기치 않게 빈 리스트 반환.")
        return []

    p_info = pkts_info_list[0]
    

    seq = p_info["seq"] # encoder에서 1로 설정됨
    payload_chunk = p_info["payload"]

    # 새 프레임 내용: SEQ(1B) + PAYLOAD_CHUNK
    frame_content = bytes([seq]) + payload_chunk
    
    return [frame_content] # 단일 프레임 내용을 리스트에 담아 반환