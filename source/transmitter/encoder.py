# encoder.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import struct, zlib, logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# _FMT: 기존 "<Ihhhhhhhhhff" (12개 필드)
# 여기에 고도(altitude)를 위한 'h' (signed short, 2바이트) 추가
# ts(I), acc(hhh), gyro(hhh), angle(hhh), lat(f), lon(f), alt(h)
_FMT = "<Ihhhhhhhhhffh"  # 총 13개 필드, 마지막 'h'가 고도

_FIELDS = (
    ("ts",        1),       # Unix timestamp (int)
    ("accel.ax",  1000),    # short
    ("accel.ay",  1000),    # short
    ("accel.az",  1000),    # short
    ("gyro.gx",   10),      # short
    ("gyro.gy",   10),      # short
    ("gyro.gz",   10),      # short
    ("angle.roll", 10),     # short
    ("angle.pitch",10),     # short
    ("angle.yaw",  10),     # short
    ("gps.lat",   1.0),     # float
    ("gps.lon",   1.0),     # float
    ("gps.altitude", 10)    # 고도: float 값을 10 곱해서 short로 저장, 스케일은 10 (나중에 10으로 나눔)
)

def _extract(src: Dict[str, Any], dotted: str):
    parts = dotted.split('.')
    v = src
    for p_idx, p in enumerate(parts):
        try:
            v = v[p]
        except KeyError:
            # 경로 중간에 키가 없는 경우 더 명확한 오류 메시지 제공
            missing_path = ".".join(parts[:p_idx+1])
            raise KeyError(f"키 '{missing_path}'가 데이터에 없습니다. 전체 경로: '{dotted}'")
        except TypeError: # v가 더 이상 딕셔너리가 아닐 때 (예: 숫자에 인덱싱 시도)
             missing_path = ".".join(parts[:p_idx+1])
             raise TypeError(f"'{missing_path}' (값: {v})는 딕셔너리가 아니므로 '{p}' 키를 찾을 수 없습니다. 전체 경로: '{dotted}'")
    return v

def compress_data(data: Dict[str, Any]) -> bytes:
    try:
        values_to_pack = []
        for field_path, scale in _FIELDS:
            raw_value = _extract(data, field_path)

            if field_path == "ts":
                # ts는 항상 정수형으로 변환
                values_to_pack.append(int(float(raw_value))) # float일 수 있으므로 float() 거친 후 int()
            elif field_path == "gps.lat" or field_path == "gps.lon":
                # 위도, 경도는 float으로 직접 사용
                values_to_pack.append(float(raw_value))
            elif field_path == "gps.altitude":
                # 고도는 float 값을 받아서 scale(10)을 곱하고 정수(short)로 변환
                # sensor_reader에서 altitude는 float(str_val)로 변환되어 있음
                scaled_value = int(float(raw_value) * scale)
                values_to_pack.append(scaled_value)
            else: # 나머지 MPU 데이터 (accel, gyro, angle)
                # scale이 정수형이면 곱한 후 int로 변환 (short로 저장되므로)
                scaled_value = int(float(raw_value) * scale)
                values_to_pack.append(scaled_value)
        
        # 패킹될 값의 개수가 _FMT와 일치하는지 확인 (디버깅용)
        if len(values_to_pack) != len(_FMT) -1 : # < 제외하고 문자 개수
             logger.error(f"패킹할 값의 개수 불일치: 기대 {len(_FMT)-1}, 실제 {len(values_to_pack)}")
             logger.error(f"  _FIELDS 정의: {len(_FIELDS)} 개, 패킹 값: {values_to_pack}")
             return b""

        packed = struct.pack(_FMT, *values_to_pack)
        compressed = zlib.compress(packed, level=9)
        logger.debug(f"데이터 압축: 원본 {len(packed)}B -> 압축 {len(compressed)}B. (ts: {data.get('ts')})")
        return compressed

    except KeyError as e:
        logger.warning(f"compress_data: 필수 키 접근 오류 {e}. 빈 바이트 반환.")
        return b""
    except (TypeError, ValueError) as e: # 형 변환 또는 값 오류
        logger.warning(f"compress_data: 데이터 타입/값 오류 {e}. (예: _extract 또는 형 변환 중). 빈 바이트 반환.")
        return b""
    except Exception as e:
        logger.error(f"compress_data: 데이터 압축 중 예기치 않은 예외 발생: {e}. 빈 바이트 반환.", exc_info=True)
        return b""


# LoRa 페이로드 청크의 최대 크기 (SEQ 1바이트 제외한 순수 페이로드)
MAX_PAYLOAD_CHUNK = 56

def split_into_packets(data: bytes, max_payload_chunk_size: int = MAX_PAYLOAD_CHUNK) -> List[Dict]:
    if not data:
        logger.debug("split_into_packets: 입력 데이터가 비어있어 빈 페이로드로 기본 패킷 반환")
        return [{"seq": 1, "payload": b""}] # seq는 현재 packetizer에서 사용 안함

    payload = data
    if len(data) > max_payload_chunk_size:
        logger.warning(f"압축된 데이터({len(data)}B)가 최대 페이로드 크기({max_payload_chunk_size}B)를 초과. 데이터를 {max_payload_chunk_size}B로 자릅니다.")
        payload = data[:max_payload_chunk_size]
    
    return [{"seq": 1, "payload": payload}]