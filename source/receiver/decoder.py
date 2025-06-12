# decoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import struct
import zlib
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# encoder.py의 상수와 정확히 일치해야 함
_FMT = "<Ihhhhhhhhhffh"
_SCALES = (
    1, 1000, 1000, 1000, 10, 10, 10, 10, 10, 10, 1.0, 1.0, 10.0
)

def _unpack_and_reconstruct(buf: bytes) -> Optional[Dict[str, Any]]:
    """언패킹 및 딕셔너리 재구성 로직 (공통 함수)"""
    try:
        # 길이 검사
        expected_len = struct.calcsize(_FMT)
        if len(buf) != expected_len:
            logger.error(f"복원 실패: 데이터 길이 불일치. 기대: {expected_len}B, 실제: {len(buf)}B.")
            return None

        # 언패킹 및 스케일링
        unpacked_values = struct.unpack(_FMT, buf)
        scaled_values = [u / s for u, s in zip(unpacked_values, _SCALES)]
        
        ts, ax, ay, az, gx, gy, gz, roll, pitch, yaw, lat, lon, alt = scaled_values

        # 결과 딕셔너리 생성
        return {
            "ts": ts,
            "accel": {"ax": ax, "ay": ay, "az": az},
            "gyro":  {"gx": gx, "gy": gy, "gz": gz},
            "angle": {"roll": roll, "pitch": pitch, "yaw": yaw},
            "gps":   {"lat": lat, "lon": lon, "altitude": alt},
        }
    except struct.error as e:
        logger.error(f"Struct 언패킹 실패: {e}. (Input buf len: {len(buf)}B)")
        return None

def bam_decode(code_byte: bytes) -> Dict[str, Any]:
    """BAM 압축 해제 스텁(stub) 함수."""
    code = int.from_bytes(code_byte, "little")
    logger.debug(f"BAM 압축 해제 실행 (스텁): 수신 코드 {code}")
    return {"type": "bam_decoded", "code": code, "note": "Full data reconstruction is not implemented."}

def decode(payload: bytes, method: str = "zlib") -> Optional[Dict[str, Any]]:
    """
    [메인 함수] receiver가 호출할 진입점.
    수신된 페이로드를 지정된 방법으로 압축 해제/디코딩합니다.
    """
    try:
        if method == "none":
            logger.debug(f"압축 해제 방법: none (원본 {len(payload)}B)")
            return _unpack_and_reconstruct(payload)

        elif method == "zlib":
            decompressed_buf = zlib.decompress(payload)
            logger.debug(f"압축 해제 방법: zlib (압축 {len(payload)}B -> 원본 {len(decompressed_buf)}B)")
            return _unpack_and_reconstruct(decompressed_buf)

        elif method == "bam":
            logger.debug(f"압축 해제 방법: bam (압축 {len(payload)}B)")
            return bam_decode(payload)

        else:
            logger.error(f"알 수 없는 압축 해제 method: '{method}'")
            return None
            
    except zlib.error as e:
        logger.error(f"Zlib 압축 해제 실패: {e}. (Input buf len: {len(payload)}B)")
        return None
    except Exception as e:
        logger.error(f"예기치 않은 복원 실패 ({method} 방식): {e}. (Input buf len: {len(payload)}B)", exc_info=True)
        return None