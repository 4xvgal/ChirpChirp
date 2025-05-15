# encoder.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import struct, zlib, logging # logging 추가
from typing import Dict, Any, List

logger = logging.getLogger(__name__) # encoder.py의 로거

_FMT = "<Ihhhhhhhhhff"
_FIELDS = (
    ("ts",        1),
    ("accel.ax",  1000),
    ("accel.ay",  1000),
    ("accel.az",  1000),
    ("gyro.gx",   10),
    ("gyro.gy",   10),
    ("gyro.gz",   10),
    ("angle.roll", 10),
    ("angle.pitch",10),
    ("angle.yaw", 10),
    ("gps.lat",   1.0),
    ("gps.lon",   1.0),
)

def _extract(src: Dict[str, Any], dotted: str):
    parts = dotted.split('.')
    v = src
    for p in parts:
        v = v[p] # 여기서 KeyError 발생 가능
    return v

def compress_data(data: Dict[str, Any]) -> bytes:
    # data 딕셔너리에서 필요한 키가 모두 있는지 미리 확인하는 것이 좋음
    # 예: 필수 키 누락 시 로깅하고 빈 바이트 반환
    try:
        # 모든 필수 경로가 data에 존재하는지 확인
        for field_name, _ in _FIELDS:
            _extract(data, field_name) # 존재하지 않으면 여기서 KeyError 발생

        packed = struct.pack(
            _FMT,
            int(data["ts"]), # data["ts"]가 없을 수도 있음
            *[int(_extract(data, k) * scale) if isinstance(scale, int)
              else float(_extract(data, k)) for k, scale in _FIELDS[1:]]
        )
        return zlib.compress(packed, level=9)
    except KeyError as e:
        logger.warning(f"compress_data: 필수 키 '{e}' 누락. 빈 바이트 반환.")
        return b""
    except Exception as e:
        logger.error(f"compress_data: 데이터 압축 중 예외 발생: {e}. 빈 바이트 반환.")
        return b""


# LoRa 페이로드 청크의 최대 크기 (SEQ 1바이트 제외한 순수 페이로드)
MAX_PAYLOAD_CHUNK = 56 # 요구사항에 따라 56으로 변경

# 56 넘으면 그냥 잘라버림림
def split_into_packets(data: bytes, max_payload_chunk_size: int = MAX_PAYLOAD_CHUNK) -> List[Dict]:

    if not data:
        # 비어있는 데이터에 대해서도 seq:1, 빈 페이로드로 반환하여 일관성 유지
        return [{"seq": 1, "payload": b""}]

    payload = data
    if len(data) > max_payload_chunk_size:
        logger.warning(f"압축된 데이터({len(data)}B)가 최대 페이로드 크기({max_payload_chunk_size}B)를 초과합니다. 데이터를 자릅니다.")
        payload = data[:max_payload_chunk_size]
    
    # 'total' 필드는 더 이상 사용되지 않음
    return [{"seq": 1, "payload": payload}]