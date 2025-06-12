# encoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import struct
import zlib
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

_FMT = "<Ihhhhhhhhhffh"  # 32 B
_FIELDS = (
    ("ts", 1), ("accel.ax", 1000), ("accel.ay", 1000), ("accel.az", 1000),
    ("gyro.gx", 10), ("gyro.gy", 10), ("gyro.gz", 10), ("angle.roll", 10),
    ("angle.pitch", 10), ("angle.yaw", 10), ("gps.lat", 1.0), ("gps.lon", 1.0),
    ("gps.altitude", 10.0)
)

# --- [추가] PDR 테스트용 더미 페이로드 크기 정의 ---
_DUMMY_SIZE_24B = 24  # 원본 32B 대비 25% 감소
_DUMMY_SIZE_16B = 16  # 원본 32B 대비 50% 감소
_DUMMY_SIZE_8B = 8   # 원본 32B 대비 75% 감소
# --- [추가] 끝 ---

def _extract(src: Dict[str, Any], dotted: str):
    # ... (기존 코드와 동일)
    parts = dotted.split('.')
    v = src
    for p_idx, p in enumerate(parts):
        try:
            v = v[p]
        except KeyError:
            missing_path = ".".join(parts[:p_idx+1])
            raise KeyError(f"키 '{missing_path}'가 데이터에 없습니다.")
        except TypeError:
             missing_path = ".".join(parts[:p_idx+1])
             raise TypeError(f"'{missing_path}' (값: {v})는 딕셔너리가 아닙니다.")
    return v


def bam_encode(vector: Tuple) -> int:
    # ... (기존 코드와 동일)
    logger.debug(f"BAM 압축 실행 (스텁): 입력 벡터 크기 {len(vector)}")
    ts = vector[0]
    return ts & 0xFF


def encode_data(data: Dict[str, Any]) -> bytes:
    # ... (기존 코드와 동일)
    try:
        values_to_pack = []
        for field_path, scale in _FIELDS:
            raw_value = _extract(data, field_path)
            if field_path == "ts":
                values_to_pack.append(int(float(raw_value)))
            elif field_path in ["gps.lat", "gps.lon"]:
                values_to_pack.append(float(raw_value))
            else:
                scaled_value = int(float(raw_value) * scale)
                values_to_pack.append(scaled_value)
        packed = struct.pack(_FMT, *values_to_pack)
        logger.debug(f"데이터 인코딩 (struct.pack): {len(packed)}B 생성됨.")
        return packed
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"encode_data: 데이터 인코딩 중 오류: {e}. 빈 바이트 반환.")
        return b""
    except Exception as e:
        logger.error(f"encode_data: 예기치 않은 예외: {e}. 빈 바이트 반환.", exc_info=True)
        return b""

def compress_layer(payload: bytes, method: str) -> bytes:
    """압축 레이어. method에 따라 다른 압축 알고리즘을 적용합니다."""
    if method == "none":
        logger.debug("압축 방법: none (패스스루)")
        return payload
    elif method == "zlib":
        compressed = zlib.compress(payload, level=9)
        logger.debug(f"압축 방법: zlib (원본 {len(payload)}B -> 압축 {len(compressed)}B)")
        return compressed
    elif method == "bam":
        try:
            vec = struct.unpack(_FMT, payload)
            code = bam_encode(vec)
            return code.to_bytes(1, "little")
        except struct.error as e:
            logger.error(f"BAM 압축을 위한 언패킹 실패: {e}. payload 길이: {len(payload)}B")
            return b''
    # --- [추가] PDR 테스트용 더미 모드 ---
    elif method == "dummy_24b":
        logger.debug(f"압축 방법: dummy_24b (고정 24B 페이로드 생성)")
        return b'\xAA' * _DUMMY_SIZE_24B
    elif method == "dummy_16b":
        logger.debug(f"압축 방법: dummy_16b (고정 16B 페이로드 생성)")
        return b'\xBB' * _DUMMY_SIZE_16B
    elif method == "dummy_8b":
        logger.debug(f"압축 방법: dummy_8b (고정 8B 페이로드 생성)")
        return b'\xCC' * _DUMMY_SIZE_8B
    # --- [추가] 끝 ---
    else:
        raise ValueError(f"알 수 없는 압축 method: '{method}'")

def encode(dct: Dict[str, Any], method: str = "zlib") -> bytes:
    """[메인 함수] sender가 호출할 진입점."""
    packed_data = encode_data(dct)
    if not packed_data:
        return b""
    return compress_layer(packed_data, method=method)