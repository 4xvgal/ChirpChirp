# decoder.py
# -*- coding: utf-8 -*-
"""
zlib‑압축, struct 포맷 해제 → dict 복원
포맷·스케일 값은 encoder.py 와 반드시 동일해야 함
"""
from __future__ import annotations
import struct, zlib, logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# encoder.py의 _FMT와 정확히 일치해야 함
# <Ihhhhhhhhhffh (ts, MPU 9개, lat, lon, altitude)
_FMT = "<Ihhhhhhhhhffh"  # 마지막 'h'가 고도

# encoder.py의 _FIELDS 순서 및 스케일과 일치해야 함
# 고도(altitude)는 encoder에서 10을 곱해서 short로 저장했으므로,
# 여기서 10.0으로 나누어 float으로 복원
_SCALES = (
    1,       # ts
    1000,    # ax
    1000,    # ay
    1000,    # az
    10,      # gx
    10,      # gy
    10,      # gz
    10,      # roll
    10,      # pitch
    10,      # yaw
    1.0,     # lat (float)
    1.0,     # lon (float)
    10.0     # altitude (short를 10.0으로 나눠 float 복원)
)

def decompress_data(buf: bytes) -> Optional[Dict[str, Any]]:
    try:
        # 1. 압축 해제
        decompressed_buf = zlib.decompress(buf)

        # 2. 압축 해제된 데이터의 길이 검사
        expected_len = struct.calcsize(_FMT)
        if len(decompressed_buf) != expected_len:
            logger.error(f"복원 실패: 압축 해제된 데이터 길이 불일치. 기대: {expected_len}B, 실제: {len(decompressed_buf)}B. (Input buf len: {len(buf)}B)")
            return None

        # 3. 언패킹
        unpacked_values = struct.unpack(_FMT, decompressed_buf)
        
        # 4. 스케일링 및 변수 할당 (encoder.py의 _FIELDS 순서와 동일하게)
        # ts, ax, ay, az, gx, gy, gz, roll, pitch, yaw, lat, lon, altitude
        # 총 13개 값
        if len(unpacked_values) != len(_SCALES):
             logger.error(f"복원 실패: 언패킹된 값의 개수({len(unpacked_values)})와 스케일 개수({len(_SCALES)}) 불일치.")
             return None

        scaled_values = [u / s for u, s in zip(unpacked_values, _SCALES)]
        
        ts_val = scaled_values[0]
        ax_val = scaled_values[1]
        ay_val = scaled_values[2]
        az_val = scaled_values[3]
        gx_val = scaled_values[4]
        gy_val = scaled_values[5]
        gz_val = scaled_values[6]
        roll_val = scaled_values[7]
        pitch_val = scaled_values[8]
        yaw_val = scaled_values[9]
        lat_val = scaled_values[10]
        lon_val = scaled_values[11]
        alt_val = scaled_values[12] # 새로 추가된 고도 값

        # 5. 결과 딕셔너리 생성
        return {
            "ts": ts_val,
            "accel": {"ax": ax_val, "ay": ay_val, "az": az_val},
            "gyro":  {"gx": gx_val, "gy": gy_val, "gz": gz_val},
            "angle": {"roll": roll_val, "pitch": pitch_val, "yaw": yaw_val},
            "gps":   {
                "lat": lat_val,
                "lon": lon_val,
                "altitude": alt_val # 고도 데이터 추가
            },
        }
    except zlib.error as e:
        logger.error(f"Zlib 압축 해제 실패: {e}. (Input buf len: {len(buf)}B)")
        return None
    except struct.error as e:
        # decompressed_buf가 정의되었는지 확인 후 로깅
        decomp_len_str = str(len(decompressed_buf)) if 'decompressed_buf' in locals() else 'N/A (decompression failed)'
        logger.error(f"Struct 언패킹 실패: {e}. 압축 해제된 buf 길이: {decomp_len_str} (기대: {struct.calcsize(_FMT)}B). (Input buf len: {len(buf)}B)")
        return None
    except Exception as e:
        logger.error(f"예기치 않은 복원 실패: {e}. (Input buf len: {len(buf)}B)", exc_info=True) # 상세한 오류를 위해 exc_info 추가
        return None