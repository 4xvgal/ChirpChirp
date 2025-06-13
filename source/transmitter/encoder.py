# encoder.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import struct
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# _FMT와 _FIELDS는 변경 없이 그대로 사용됩니다.
_FMT = "<Ihhhhhhhhhffh"
_FIELDS = (
    ("ts", 1), ("accel.ax", 1000), ("accel.ay", 1000), ("accel.az", 1000),
    ("gyro.gx", 10), ("gyro.gy", 10), ("gyro.gz", 10),
    ("angle.roll", 10), ("angle.pitch", 10), ("angle.yaw", 10),
    ("gps.lat", 1.0), ("gps.lon", 1.0), ("gps.altitude", 10)
)

# LoRa 프레임 콘텐츠의 최대 크기 (SEQ 1바이트 + PAYLOAD)
# 예: E22 모듈의 최대 전송 크기가 58바이트라고 가정할 때, LENGTH 바이트(1)를 제외한 크기
MAX_FRAME_CONTENT_SIZE = 57

def _extract(src: Dict[str, Any], dotted: str):
    parts = dotted.split('.')
    v = src
    for p_idx, p in enumerate(parts):
        try:
            v = v[p]
        except KeyError:
            missing_path = ".".join(parts[:p_idx+1])
            raise KeyError(f"키 '{missing_path}'가 데이터에 없습니다. 전체 경로: '{dotted}'")
        except TypeError:
             missing_path = ".".join(parts[:p_idx+1])
             raise TypeError(f"'{missing_path}' (값: {v})는 딕셔너리가 아니므로 '{p}' 키를 찾을 수 없습니다.")
    return v

def _pack_data(data: Dict[str, Any]) -> bytes:
    """센서 데이터를 struct.pack을 사용하여 raw 바이너리 데이터로 변환합니다."""
    try:
        values_to_pack = []
        for field_path, scale in _FIELDS:
            raw_value = _extract(data, field_path)
            if field_path in ("ts", "gps.lat", "gps.lon"):
                values_to_pack.append(float(raw_value) if field_path != "ts" else int(float(raw_value)))
            else:
                values_to_pack.append(int(float(raw_value) * scale))

        if len(values_to_pack) != len(_FMT) - 1:
             logger.error(f"패킹할 값의 개수 불일치: 기대 {len(_FMT)-1}, 실제 {len(values_to_pack)}")
             return b""

        packed = struct.pack(_FMT, *values_to_pack)
        logger.debug(f"데이터 패킹 완료: 원본 {len(packed)}B. (ts: {data.get('ts')})")
        return packed

    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"_pack_data: 데이터 처리 오류 {e}. 빈 바이트 반환.")
        return b""
    except Exception as e:
        logger.error(f"_pack_data: 예기치 않은 예외 발생: {e}.", exc_info=True)
        return b""

def compress_layer(packed_data: bytes, mode: str = "none") -> Optional[bytes]:
    """
    압축/인코딩 레이어. 선택된 모드에 따라 데이터를 변환합니다.
    - "none": 아무 처리 없이 원본 데이터를 반환합니다 (raw).
    - "bam": 향후 구현될 BAM 인코더를 위한 인터페이스 (현재는 원본 데이터 반환).
    """
    if mode == "none":
        logger.debug(f"압축 모드 'none': 원본 데이터 {len(packed_data)}B 사용.")
        return packed_data
    
    elif mode == "bam":
        # --- BAM 인코딩 로직을 여기에 구현 ---
        logger.warning("압축 모드 'bam'이 선택되었으나 아직 구현되지 않았습니다. 원본 데이터를 사용합니다.")
        # encoded_data = bam_encode(packed_data) # 예시
        # return encoded_data
        return packed_data # 임시로 원본 반환
        
    else:
        logger.error(f"알 수 없는 압축 모드: '{mode}'.")
        return None

def create_frame(sample: Dict[str, Any], message_seq: int, compression_mode: str) -> Optional[bytes]:
    """
    센서 샘플로부터 최종 전송 프레임(콘텐츠)을 생성하는 단일 인터페이스.
    [ MESSAGE_SEQ (1B) | PAYLOAD_CHUNK ]
    """
    # 1. 데이터를 raw 바이너리로 패킹
    packed_blob = _pack_data(sample)
    if not packed_blob:
        logger.warning(f"MESSAGE_SEQ {message_seq}: 데이터 패킹 실패. 빈 프레임 반환.")
        return None
        
    # 2. 선택된 압축/인코딩 레이어 적용
    payload_chunk = compress_layer(packed_blob, mode=compression_mode)
    if payload_chunk is None:
        logger.error(f"MESSAGE_SEQ {message_seq}: 압축/인코딩 레이어 실패. 빈 프레임 반환.")
        return None

    # 3. 프레임 콘텐츠 생성: [SEQ | PAYLOAD]
    frame_content = bytes([message_seq % 256]) + payload_chunk

    # 4. 프레임 크기 확인 및 자르기
    if len(frame_content) > MAX_FRAME_CONTENT_SIZE:
        logger.warning(
            f"생성된 프레임 콘텐츠({len(frame_content)}B)가 최대 크기({MAX_FRAME_CONTENT_SIZE}B)를 초과. "
            f"데이터를 자릅니다."
        )
        frame_content = frame_content[:MAX_FRAME_CONTENT_SIZE]

    logger.debug(f"프레임 생성 완료 (mode: {compression_mode}): MESSAGE_SEQ={message_seq % 256}, 최종 콘텐츠 길이={len(frame_content)}B")
    return frame_content