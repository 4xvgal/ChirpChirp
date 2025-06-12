# decoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import struct
import zlib
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_FMT = "<Ihhhhhhhhhffh"
_SCALES = (
    1, 1000, 1000, 1000, 10, 10, 10, 10, 10, 10, 1.0, 1.0, 10.0
)

# --- [추가] PDR 테스트용 더미 페이로드 크기 정의 ---
_DUMMY_SIZE_24B = 24
_DUMMY_SIZE_16B = 16
_DUMMY_SIZE_8B = 8
# --- [추가] 끝 ---

def _unpack_and_reconstruct(buf: bytes) -> Optional[Dict[str, Any]]:
    # ... (기존 코드와 동일)
    try:
        expected_len = struct.calcsize(_FMT)
        if len(buf) != expected_len:
            logger.error(f"복원 실패: 데이터 길이 불일치. 기대: {expected_len}B, 실제: {len(buf)}B.")
            return None
        unpacked_values = struct.unpack(_FMT, buf)
        scaled_values = [u / s for u, s in zip(unpacked_values, _SCALES)]
        ts, ax, ay, az, gx, gy, gz, roll, pitch, yaw, lat, lon, alt = scaled_values
        return {
            "ts": ts, "accel": {"ax": ax, "ay": ay, "az": az},
            "gyro":  {"gx": gx, "gy": gy, "gz": gz}, "angle": {"roll": roll, "pitch": pitch, "yaw": yaw},
            "gps":   {"lat": lat, "lon": lon, "altitude": alt},
        }
    except struct.error as e:
        logger.error(f"Struct 언패킹 실패: {e}. (Input buf len: {len(buf)}B)")
        return None

def bam_decode(code_byte: bytes) -> Dict[str, Any]:
    # ... (기존 코드와 동일)
    code = int.from_bytes(code_byte, "little")
    logger.debug(f"BAM 압축 해제 실행 (스텁): 수신 코드 {code}")
    return {"type": "bam_decoded", "code": code}

def decode(payload: bytes, method: str = "zlib") -> Optional[Dict[str, Any]]:
    """[메인 함수] receiver가 호출할 진입점."""
    try:
        if method == "none":
            return _unpack_and_reconstruct(payload)
        elif method == "zlib":
            decompressed_buf = zlib.decompress(payload)
            return _unpack_and_reconstruct(decompressed_buf)
        elif method == "bam":
            return bam_decode(payload)
        # --- [추가] PDR 테스트용 더미 모드 검증 ---
        elif method == "dummy_24b":
            if len(payload) == _DUMMY_SIZE_24B:
                return {"type": "dummy_decoded", "size": len(payload), "method": method}
            else:
                logger.error(f"Dummy 페이로드 길이 불일치. 기대: {_DUMMY_SIZE_24B}B, 실제: {len(payload)}B")
                return None
        elif method == "dummy_16b":
            if len(payload) == _DUMMY_SIZE_16B:
                return {"type": "dummy_decoded", "size": len(payload), "method": method}
            else:
                logger.error(f"Dummy 페이로드 길이 불일치. 기대: {_DUMMY_SIZE_16B}B, 실제: {len(payload)}B")
                return None
        elif method == "dummy_8b":
            if len(payload) == _DUMMY_SIZE_8B:
                return {"type": "dummy_decoded", "size": len(payload), "method": method}
            else:
                logger.error(f"Dummy 페이로드 길이 불일치. 기대: {_DUMMY_SIZE_8B}B, 실제: {len(payload)}B")
                return None
        # --- [추가] 끝 ---
        else:
            logger.error(f"알 수 없는 압축 해제 method: '{method}'")
            return None
    except zlib.error as e:
        logger.error(f"Zlib 압축 해제 실패: {e}. (Input buf len: {len(payload)}B)")
        return None
    except Exception as e:
        logger.error(f"예기치 않은 복원 실패 ({method} 방식): {e}. (Input buf len: {len(payload)}B)", exc_info=True)
        return None