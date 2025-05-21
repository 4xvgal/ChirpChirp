# packetizer.py
# -*- coding: utf-8 -*-
"""
packetizer.py
• encoder.compress_data() 결과를 LoRa 프레임(SEQ+PAYLOAD_CHUNK)으로 변환.
"""
from __future__ import annotations
import logging
from typing import Dict, Any, List # List, Dict, Any 임포트 추가

try:
    from .encoder import compress_data, split_into_packets, MAX_PAYLOAD_CHUNK
except ImportError: # 단독 실행 또는 PYTHONPATH 문제 시
    from encoder import compress_data, split_into_packets, MAX_PAYLOAD_CHUNK


logger = logging.getLogger(__name__)

# make_frames 함수 수정: pkt_id 대신 message_seq를 받고, 이를 프레임 SEQ로 사용
def make_frames(sample: Dict[str, Any], message_seq: int) -> List[bytes]:
    """
    센서 dict -> zlib 압축 -> 단일 프레임을 다음 바이트 시퀀스로 변환 (리스트에 담아 반환):
    [ MESSAGE_SEQ (1B) | PAYLOAD_CHUNK ]
    message_seq는 이 메시지의 고유 식별자입니다.
    encoder.py의 split_into_packets가 반환하는 내부 "seq"는 무시됩니다.
    """
    blob = compress_data(sample)
    if not blob:
        # 이전에는 pkt_id를 사용했지만, 이제 message_seq를 사용
        logger.warning(f"MESSAGE_SEQ {message_seq}: compress_data 결과가 비어있어 빈 프레임 리스트 반환")
        return []

    pkts_info_list = split_into_packets(blob) 

    if not pkts_info_list:
        logger.error(f"MESSAGE_SEQ {message_seq}: split_into_packets가 예기치 않게 빈 리스트 반환.")
        return []

    p_info = pkts_info_list[0]
    # encoder.py가 반환하는 p_info["seq"]는 현재 사용하지 않음 (항상 1)
    payload_chunk = p_info["payload"]

    # 새 프레임 내용: MESSAGE_SEQ(1B) + PAYLOAD_CHUNK
    # message_seq를 0-255 범위로 만듦
    frame_content = bytes([message_seq % 256]) + payload_chunk
    
    logger.debug(f"Frame created with MESSAGE_SEQ={message_seq % 256}, Payload_len={len(payload_chunk)}")
    return [frame_content]