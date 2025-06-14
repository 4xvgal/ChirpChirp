# decoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import struct
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# --- 'raw' 모드 (raw 데이터)를 위한 설정 ---
_RAW_FMT = "<Ihhhhhhhhhffh"
_RAW_SCALES = (
    1, 1000, 1000, 1000, 10, 10, 10, 10, 10, 10, 1.0, 1.0, 10.0
)
_RAW_EXPECTED_LEN = struct.calcsize(_RAW_FMT) # 34 바이트

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

def decode_frame_payload(payload_chunk: bytes, mode: str) -> Optional[Dict[str, Any]]:
    """
    수신된 프레임 페이로드를 디코딩하여 데이터 딕셔너리로 복원합니다.
    페이로드의 길이를 보고 센서 데이터인지 더미 데이터인지 추측합니다.
    """
    payload_len = len(payload_chunk)

    if mode == 'raw':
        # 'raw' 모드에서는 페이로드 길이가 34바이트이면 센서 데이터로 간주
        if payload_len == _RAW_EXPECTED_LEN:
            logger.debug(f"페이로드 길이가 {_RAW_EXPECTED_LEN}B이므로 센서 데이터로 간주하여 'raw' 디코딩합니다.")
            return _decode_raw_payload(payload_chunk)
        else:
            # 그 외 길이는 더미 데이터로 간주하고, 더미 데이터임을 알리는 딕셔너리 반환
            logger.info(f"페이로드 길이가 {_RAW_EXPECTED_LEN}B와 달라, {payload_len}B 크기의 더미 데이터로 간주합니다.")
            return {"type": "dummy", "size": payload_len}

    elif mode == 'bam':
        # --- BAM 디코딩 로직 인터페이스 ---
        # BAM으로 인코딩된 페이로드는 항상 더미 데이터로 간주하거나,
        # 혹은 BAM 디코더를 통해 원본 raw 데이터를 복원하는 로직을 추가할 수 있습니다.
        # 현재는 더미 데이터로만 처리합니다.
        logger.warning(f"BAM 모드는 현재 더미 데이터로만 처리됩니다 (페이로드 길이: {payload_len}B).")
        # restored_raw_bytes = _decode_bam_payload(payload_chunk) # 예시
        # if restored_raw_bytes:
        #     return _decode_raw_payload(restored_raw_bytes)
        return {"type": "dummy_bam", "size": payload_len}

    else:
        logger.error(f"알 수 없는 디코딩 모드: '{mode}'")
        return None