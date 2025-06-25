# ChirpChirp/source/receiver/decoder.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import struct
import logging
from typing import Dict, Any, Optional
import numpy as np
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from source.bam_autoencoder.feature_extractor import FeatureExtractor
    from source.bam_autoencoder.data_loader import NumpyDataLoader
except ImportError as e:
    print(f"CRITICAL: BAM 모듈 임포트 실패 (decoder.py): {e}.")
    FeatureExtractor = None; NumpyDataLoader = None

logger = logging.getLogger(__name__)

_RAW_FMT = "<Ihhhhhhhhhfff" 
_RAW_SCALES = (1, 1000, 1000, 1000, 10, 10, 10, 10, 10, 10, 1.0, 1.0, 1.0)
_RAW_EXPECTED_LEN = struct.calcsize(_RAW_FMT)

BAM_AUTOENCODER = None
SCALER = None
MODEL_INITIALIZED = False
# ⚠️ 학습 시 run_compression_experiment.py에서 사용한 MF_LAYER_DIMS와 동일하게!
DECODER_MF_LAYER_DIMS = [12, 24, 16, 8] # <--- 실제 학습된 아키텍처로 수정!
DECODER_WEIGHTS_PATH = 'data/bam_autoencoder_weights'
DECODER_SCALER_DATA_PATH = 'data/original/clean_lora_data_combined.csv'

def initialize_bam_decoder_interface(): # 함수 이름 변경
    global BAM_AUTOENCODER, SCALER, MODEL_INITIALIZED
    if MODEL_INITIALIZED: return
    if FeatureExtractor is None or NumpyDataLoader is None:
        logger.error("BAM 관련 모듈 임포트 불가. 디코더 초기화 실패.")
        return
    try:
        logger.info("BAM 디코더 인터페이스 초기화 시도...")
        autoencoder_model = FeatureExtractor(layer_dims=DECODER_MF_LAYER_DIMS)
        weights_full_path = os.path.join(project_root, DECODER_WEIGHTS_PATH)
        
        # FeatureExtractor의 load_weights 사용
        autoencoder_model.load_weights(weights_full_path)
        BAM_AUTOENCODER = autoencoder_model
        logger.info("✅ Decoder: BAM 모델 가중치 로드 성공.")

        scaler_data_full_path = os.path.join(project_root, DECODER_SCALER_DATA_PATH)
        if not os.path.exists(scaler_data_full_path):
            logger.error(f"스케일러 학습용 데이터 파일 없음: {scaler_data_full_path}")
            MODEL_INITIALIZED = False; return
        temp_loader = NumpyDataLoader(filepath=scaler_data_full_path, batch_size=1, test_size=0.999)
        SCALER = temp_loader.scaler
        logger.info("✅ Decoder: 데이터 스케일러 준비 완료.")
        MODEL_INITIALIZED = True
        logger.info("✅ BAM 디코더 인터페이스 초기화 성공!")
    except Exception as e:
        logger.error(f"BAM 디코더 인터페이스 초기화 실패: {e}", exc_info=True)
        BAM_AUTOENCODER = None; SCALER = None; MODEL_INITIALIZED = False

initialize_bam_decoder_interface()

def _decode_raw_payload(payload_chunk: bytes) -> Optional[Dict[str, Any]]:
    if len(payload_chunk) != _RAW_EXPECTED_LEN:
        logger.error(f"Raw 디코딩: 길이 불일치. 기대 {_RAW_EXPECTED_LEN}B, 실제 {len(payload_chunk)}B.")
        return None
    try:
        unpacked = struct.unpack(_RAW_FMT, payload_chunk)
        scaled_values = []
        for i, val in enumerate(unpacked):
            if i == 0: scaled_values.append(float(val))
            elif 1 <= i <= 9: scaled_values.append(val / _RAW_SCALES[i])
            else: scaled_values.append(val)
        return {
            "ts": scaled_values[0],
            "accel": {"ax": scaled_values[1], "ay": scaled_values[2], "az": scaled_values[3]},
            "gyro":  {"gx": scaled_values[4], "gy": scaled_values[5], "gz": scaled_values[6]},
            "angle": {"roll": scaled_values[7], "pitch": scaled_values[8], "yaw": scaled_values[9]},
            "gps":   {"lat": scaled_values[10], "lon": scaled_values[11], "altitude": scaled_values[12]},
        }
    except struct.error as e: logger.error(f"Raw 언패킹 실패: {e}"); return None

def _decode_bam_payload(payload_chunk: bytes) -> Optional[Dict[str, Any]]:
    if not MODEL_INITIALIZED or not BAM_AUTOENCODER or not SCALER:
        logger.error("BAM 디코더 미초기화. 디코딩 불가.")
        return {"type": "dummy_bam_decode_fail", "size": len(payload_chunk), "error": "Decoder not ready"}
    try:
        num_latent_elements = DECODER_MF_LAYER_DIMS[-1]
        pack_format = f'<I{num_latent_elements}h' # Little-endian, Unsigned Int, N Signed Shorts
        
        expected_size = struct.calcsize(pack_format)
        if expected_size != len(payload_chunk):
            logger.error(f"BAM 페이로드(16b) 크기 불일치. 기대 {expected_size}B, 실제 {len(payload_chunk)}B")
            return {"type": "dummy_bam_decode_fail", "size": len(payload_chunk), "error": "Quantized Payload size mismatch"}

        unpacked_data = struct.unpack(pack_format, payload_chunk)
        ts = float(unpacked_data[0])
        latent_vector_quantized = np.array(unpacked_data[1:], dtype=np.int16)

        # --- 16비트 역양자화 (Signed Int -> Float) ---
        latent_vector_float = [val_q / 32767.0 for val_q in latent_vector_quantized]
        latent_vector_for_decode = np.array([latent_vector_float], dtype=np.float32)

        reconstructed_scaled = latent_vector_for_decode
        for layer in reversed(BAM_AUTOENCODER.layers):
            b = layer.V @ reconstructed_scaled.T
            reconstructed_scaled = layer.transmission_function(b).T
        
        reconstructed_data_unscaled = SCALER.inverse_transform(reconstructed_scaled).flatten()
        
        return {
            "ts": ts,
            "accel": {"ax": float(reconstructed_data_unscaled[0]), "ay": float(reconstructed_data_unscaled[1]), "az": float(reconstructed_data_unscaled[2])},
            "gyro":  {"gx": float(reconstructed_data_unscaled[3]), "gy": float(reconstructed_data_unscaled[4]), "gz": float(reconstructed_data_unscaled[5])},
            "angle": {"roll": float(reconstructed_data_unscaled[6]), "pitch": float(reconstructed_data_unscaled[7]), "yaw": float(reconstructed_data_unscaled[8])},
            "gps":   {"lat": float(reconstructed_data_unscaled[9]), "lon": float(reconstructed_data_unscaled[10]), "altitude": float(reconstructed_data_unscaled[11])}
        }
    except Exception as e:
        logger.error(f"BAM 디코딩(16b 양자화) 중 오류: {e}", exc_info=True)
        return {"type": "dummy_bam_decode_fail", "size": len(payload_chunk), "error": str(e)}

def decode_frame_payload(payload_chunk: bytes, mode: str) -> Optional[Dict[str, Any]]:
    if mode == 'raw': return _decode_raw_payload(payload_chunk)
    elif mode == 'bam': return _decode_bam_payload(payload_chunk)
    else: logger.error(f"알 수 없는 디코딩 모드: '{mode}'"); return None