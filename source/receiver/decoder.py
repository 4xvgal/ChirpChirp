# decoder.py
# -*- coding: utf-8 -*-
"""
수신된 페이로드를 디코딩하여 원본 데이터 딕셔너리로 복원합니다.
인코딩 방식('none', 'bam' 등)을 자동으로 감지하거나, 외부에서 지정받아 처리합니다.
"""
from __future__ import annotations
import struct
import logging
from typing import Dict, Any, Optional

# 만약 BAM 구현이 numpy 등을 사용한다면 여기에 임포트
# import numpy as np 

logger = logging.getLogger(__name__)

# --- 'none' 모드 (raw 데이터)를 위한 설정 ---
_RAW_FMT = "<Ihhhhhhhhhffh"
_RAW_SCALES = (
    1, 1000, 1000, 1000, 10, 10, 10, 10, 10, 10, 1.0, 1.0, 10.0
)
_RAW_EXPECTED_LEN = struct.calcsize(_RAW_FMT) # 34 바이트

# --- BAM 관련 설정 및 함수 (예시) ---
# BAM 네트워크의 가중치 행렬 등은 미리 학습되어 파일로 저장되어 있어야 합니다.
# BAM_WEIGHT_MATRIX = np.load('bam_weights.npy') # 예시

def _decode_bam_payload(payload_chunk: bytes) -> Optional[bytes]:
    """
    BAM으로 인코딩된 페이로드를 디코딩하여 원본 raw 바이너리 데이터로 복원합니다.
    (이 함수는 BAM 구현에 따라 완전히 달라집니다)
    """
    logger.debug(f"BAM 디코딩 시도 (페이로드 길이: {len(payload_chunk)}B)...")
    
    # --- 여기에 실제 BAM 디코딩 로직을 구현합니다 ---
    # 1. 수신된 payload_chunk를 BAM 입력 벡터 형식으로 변환 (예: numpy 배열)
    # bam_input_vector = np.frombuffer(payload_chunk, dtype=np.int8)

    # 2. BAM 네트워크를 통해 원본 데이터 복원 (가중치 행렬 사용)
    # restored_vector = bam_decode(bam_input_vector, BAM_WEIGHT_MATRIX)
    
    # 3. 복원된 벡터를 원본 바이너리 데이터(bytes)로 변환
    # restored_raw_bytes = restored_vector.tobytes()
    
    # 지금은 BAM이 구현되지 않았으므로 에러를 반환합니다.
    logger.error("BAM 디코딩 로직이 아직 구현되지 않았습니다.")
    return None # 실제 구현 시 복원된 바이트를 반환해야 함

def _decode_raw_payload(payload_chunk: bytes) -> Optional[Dict[str, Any]]:
    """struct로 패킹된 raw 페이로드를 디코딩합니다."""
    if len(payload_chunk) != _RAW_EXPECTED_LEN:
        logger.error(f"Raw 디코딩 실패: 길이 불일치. 기대: {_RAW_EXPECTED_LEN}B, 실제: {len(payload_chunk)}B.")
        return None
    
    try:
        unpacked = struct.unpack(_RAW_FMT, payload_chunk)
        scaled = [u / s for u, s in zip(unpacked, _RAW_SCALES)]
        return {
            "ts": scaled[0],
            "accel": {"ax": scaled[1], "ay": scaled[2], "az": scaled[3]},
            "gyro":  {"gx": scaled[4], "gy": scaled[5], "gz": scaled[6]},
            "angle": {"roll": scaled[7], "pitch": scaled[8], "yaw": scaled[9]},
            "gps":   {"lat": scaled[10], "lon": scaled[11], "altitude": scaled[12]},
        }
    except struct.error as e:
        logger.error(f"Raw 데이터 언패킹 실패: {e}")
        return None

def decode_frame_payload(payload_chunk: bytes) -> Optional[Dict[str, Any]]:
    """
    수신된 프레임 페이로드를 디코딩하여 데이터 딕셔너리로 복원합니다.
    페이로드의 길이를 보고 인코딩 방식을 추측합니다.
    """
    # --- 인코딩 방식 감지 로직 ---
    # 'none' 모드는 항상 고정된 길이(_RAW_EXPECTED_LEN)를 가집니다.
    # BAM은 다른 길이를 가질 것입니다.
    
    payload_len = len(payload_chunk)
    
    if payload_len == _RAW_EXPECTED_LEN:
        # 길이가 raw 데이터 길이와 일치하면 'none' 모드로 간주
        logger.debug("페이로드 길이가 raw 데이터 길이와 일치하여 'none' 모드로 디코딩합니다.")
        return _decode_raw_payload(payload_chunk)
        
    else:
        # 그 외의 길이는 'bam' 모드로 간주
        logger.debug(f"페이로드 길이가 raw 데이터 길이와 달라({payload_len}B), 'bam' 모드로 디코딩을 시도합니다.")
        
        # 1. BAM 페이로드를 raw 바이너리로 복원
        restored_raw_bytes = _decode_bam_payload(payload_chunk)
        
        if restored_raw_bytes is None:
            logger.error("BAM 페이로드 복원에 실패했습니다.")
            return None
            
        # 2. 복원된 raw 바이너리를 최종 딕셔너리로 변환
        return _decode_raw_payload(restored_raw_bytes)