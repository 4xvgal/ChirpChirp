# ChirpChirp/source/transmitter/encoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import time
import logging
import struct
import os
import sys
import numpy as np

# 프로젝트 루트를 sys.path에 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from source.bam_autoencoder.feature_extractor import FeatureExtractor
    from source.bam_autoencoder.data_loader import NumpyDataLoader
except ImportError as e:
    print(f"CRITICAL: BAM 모듈 임포트 실패 (encoder.py): {e}. 경로를 확인하세요.")
    FeatureExtractor = None
    NumpyDataLoader = None

logger = logging.getLogger(__name__)

# --- Raw 모드 설정 ---
_RAW_FMT = "<Ihhhhhhhhhfff" # altitude float 가정 (총 34바이트)
_RAW_FIELDS_SCALES = (
    ("ts", 1), ("accel.ax", 1000), ("accel.ay", 1000), ("accel.az", 1000),
    ("gyro.gx", 10), ("gyro.gy", 10), ("gyro.gz", 10),
    ("angle.roll", 10), ("angle.pitch", 10), ("angle.yaw", 10),
    ("gps.lat", 1.0), ("gps.lon", 1.0), ("gps.altitude", 1.0)
)
MAX_FRAME_CONTENT_SIZE = 57

# --- BAM 모델 및 스케일러 초기화 ---
BAM_AUTOENCODER = None
SCALER = None
MODEL_INITIALIZED = False
# ⚠️ 학습 시 run_compression_experiment.py에서 사용한 MF_LAYER_DIMS와 동일하게!
# 예시: MF_LAYER_DIMS = [12, 16, 10, 8] (입력 12, 은닉1 16, 은닉2 10, 잠재 8)
ENCODER_MF_LAYER_DIMS = [12, 24, 16, 8] # <--- 실제 학습된 아키텍처로 수정!
ENCODER_WEIGHTS_PATH = 'data/bam_autoencoder_weights'
ENCODER_SCALER_DATA_PATH = 'data/original/clean_lora_data_combined.csv'

def initialize_bam_encoder_interface():
    global BAM_AUTOENCODER, SCALER, MODEL_INITIALIZED
    if MODEL_INITIALIZED: return
    if FeatureExtractor is None or NumpyDataLoader is None:
        logger.error("BAM 관련 모듈 임포트 불가. 인코더 초기화 실패.")
        return
    try:
        logger.info("BAM 인코더 인터페이스 초기화 시도...")
        autoencoder_model = FeatureExtractor(layer_dims=ENCODER_MF_LAYER_DIMS)
        weights_full_path = os.path.join(project_root, ENCODER_WEIGHTS_PATH)
        
        for i, layer in enumerate(autoencoder_model.layers):
            weight_file = os.path.join(weights_full_path, f'mf_layer_{i}_weights.npz') # mf_ 접두사 있는 파일명
            if not os.path.exists(weight_file):
                logger.error(f"가중치 파일 없음: {weight_file}")
                MODEL_INITIALIZED = False; return
            data = np.load(weight_file)
            layer.W = data['W']; layer.V = data['V']
        BAM_AUTOENCODER = autoencoder_model
        logger.info("✅ Encoder: BAM 모델 가중치 로드 성공.")

        scaler_data_full_path = os.path.join(project_root, ENCODER_SCALER_DATA_PATH)
        if not os.path.exists(scaler_data_full_path):
            logger.error(f"스케일러 학습용 데이터 파일 없음: {scaler_data_full_path}")
            MODEL_INITIALIZED = False; return
        temp_loader = NumpyDataLoader(filepath=scaler_data_full_path, batch_size=1, test_size=0.999)
        SCALER = temp_loader.scaler
        logger.info("✅ Encoder: 데이터 스케일러 준비 완료.")
        MODEL_INITIALIZED = True
        logger.info("✅ BAM 인코더 인터페이스 초기화 성공!")
    except Exception as e:
        logger.error(f"BAM 인코더 인터페이스 초기화 실패: {e}", exc_info=True)
        BAM_AUTOENCODER = None; SCALER = None; MODEL_INITIALIZED = False

initialize_bam_encoder_interface()

def _extract(src: Dict[str, Any], dotted: str, default_val=0.0):
    parts = dotted.split('.'); v = src
    for p_idx, p in enumerate(parts):
        try: v = v[p]
        except (KeyError, TypeError): return default_val
    return v

def _pack_raw_data(data: Dict[str, Any]) -> bytes:
    try:
        values_to_pack = []
        for field_path, scale in _RAW_FIELDS_SCALES:
            raw_value = _extract(data, field_path)
            if field_path == "ts": values_to_pack.append(int(float(raw_value)))
            elif field_path.startswith("gps."): values_to_pack.append(float(raw_value))
            else: values_to_pack.append(int(float(raw_value) * scale))
        return struct.pack(_RAW_FMT, *values_to_pack)
    except Exception as e:
        logger.error(f"Raw 데이터 패킹 오류: {e}", exc_info=True)
        return b'\x00' * struct.calcsize(_RAW_FMT)

def _encode_bam_data(sample_dict: Dict[str, Any]) -> Optional[bytes]:
    if not MODEL_INITIALIZED or not BAM_AUTOENCODER or not SCALER:
        logger.error("BAM 인코더가 초기화되지 않았습니다.")
        return None
    try:
        feature_values = [
            _extract(sample_dict, "accel.ax"), _extract(sample_dict, "accel.ay"), _extract(sample_dict, "accel.az"),
            _extract(sample_dict, "gyro.gx"), _extract(sample_dict, "gyro.gy"), _extract(sample_dict, "gyro.gz"),
            _extract(sample_dict, "angle.roll"), _extract(sample_dict, "angle.pitch"), _extract(sample_dict, "angle.yaw"),
            _extract(sample_dict, "gps.lat"), _extract(sample_dict, "gps.lon"), _extract(sample_dict, "gps.altitude")
        ]
        sensor_array = np.array([feature_values], dtype=np.float32)
        scaled_data = SCALER.transform(sensor_array)
        latent_vector_float = BAM_AUTOENCODER.predict(scaled_data).flatten()

        # --- 16비트 양자화 (Float -> 16-bit Signed Int) ---
        latent_vector_quantized = []
        for val in latent_vector_float:
            quantized_val = int(round(np.clip(val, -1.0, 1.0) * 32767))
            latent_vector_quantized.append(quantized_val)
        
        ts_val = int(_extract(sample_dict, "ts", default_val=time.time()))
        
        # --- 패킹: 타임스탬프(I, 4B) + 잠재벡터(h, 2B) * N개 ---
        # Little-endian '<', Unsigned Int 'I', N Signed Shorts 'h'
        pack_format = f'<I{len(latent_vector_quantized)}h' 
        payload_bytes = struct.pack(pack_format, ts_val, *latent_vector_quantized)
        
        logger.info(f"BAM 모드(16b 양자화): 원본(raw) 약 {struct.calcsize(_RAW_FMT)}B -> 압축 {len(payload_bytes)}B (ts포함)")
        return payload_bytes

    except Exception as e:
        logger.error(f"BAM 인코딩(16b 양자화) 중 오류: {e}", exc_info=True)
        return None

def create_frame(sample: Dict[str, Any], message_seq: int, compression_mode: str, payload_size: int = 0) -> Optional[bytes]:
    payload_chunk = b''
    if payload_size == 0:
        if compression_mode == "raw":
            payload_chunk = _pack_raw_data(sample)
            if not payload_chunk : return None
        elif compression_mode == "bam":
            payload_chunk = _encode_bam_data(sample)
            if not payload_chunk:
                logger.warning(f"BAM 인코딩 실패, Raw 모드로 대체 시도. (SEQ: {message_seq})")
                payload_chunk = _pack_raw_data(sample)
                if not payload_chunk: return None
        else:
            logger.error(f"알 수 없는 압축 모드: {compression_mode}"); return None
    elif payload_size > 0:
        payload_chunk = os.urandom(payload_size)
    else: logger.error(f"잘못된 payload_size: {payload_size}"); return None

    frame_content = bytes([message_seq % 256]) + payload_chunk
    if len(frame_content) > MAX_FRAME_CONTENT_SIZE:
        logger.warning(f"생성된 프레임({len(frame_content)}B)이 최대 크기({MAX_FRAME_CONTENT_SIZE}B) 초과. 자릅니다.")
        frame_content = frame_content[:MAX_FRAME_CONTENT_SIZE]
    return frame_content