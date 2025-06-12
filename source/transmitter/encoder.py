# encoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import struct
import zlib
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

# _FMT, _FIELDS는 decoder.py와 완벽히 동기화되어야 합니다.
_FMT = "<Ihhhhhhhhhffh"  # 32 B
_FIELDS = (
    ("ts", 1),
    ("accel.ax", 1000),
    ("accel.ay", 1000),
    ("accel.az", 1000),
    ("gyro.gx", 10),
    ("gyro.gy", 10),
    ("gyro.gz", 10),
    ("angle.roll", 10),
    ("angle.pitch", 10),
    ("angle.yaw", 10),
    ("gps.lat", 1.0),
    ("gps.lon", 1.0),
    ("gps.altitude", 10.0)
)

def _extract(src: Dict[str, Any], dotted: str):
    """딕셔너리에서 중첩된 키를 사용하여 값을 추출합니다."""
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
    """BAM 압축 스텁(stub) 함수. 13개 값을 1바이트 코드로 매핑합니다."""
    logger.debug(f"BAM 압축 실행 (스텁): 입력 벡터 크기 {len(vector)}")
    # 예시: 타임스탬프의 마지막 8비트를 코드로 사용
    ts = vector[0]
    code = ts & 0xFF
    return code

def encode_data(data: Dict[str, Any]) -> bytes:
    """센서 딕셔너리를 압축되지 않은 32바이트 struct 바이트로 인코딩합니다."""
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
            compressed = code.to_bytes(1, "little")
            logger.debug(f"압축 방법: bam (원본 {len(payload)}B -> 압축 {len(compressed)}B)")
            return compressed
        except struct.error as e:
            logger.error(f"BAM 압축을 위한 언패킹 실패: {e}. payload 길이: {len(payload)}B")
            return b''
    else:
        raise ValueError(f"알 수 없는 압축 method: '{method}'")

def encode(dct: Dict[str, Any], method: str = "zlib") -> bytes:
    """
    [메인 함수] sender가 호출할 진입점.
    데이터를 인코딩하고 지정된 방법으로 압축하여 최종 전송 페이로드를 생성합니다.
    """
    # 1. 센서 데이터를 32바이트 struct로 인코딩
    packed_data = encode_data(dct)
    if not packed_data:
        return b""

    # 2. 압축 레이어를 통해 최종 페이로드 생성
    return compress_layer(packed_data, method=method)