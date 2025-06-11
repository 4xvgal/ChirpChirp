# ChirpChirp/source/receiver/rx_logger.py
# -*- coding: utf-8 -*-

import csv
import os
import datetime
import logging
# import json # decoded_payload_dict를 직접 필드화하므로 json.dumps는 이제 notes용으로만 필요할 수 있음
from typing import Optional, Dict, Any

rx_internal_logger = logging.getLogger(__name__)

CURRENT_DIR_OF_RX_LOGGER = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR_OF_RX_LOGGER))
LOG_DIR_ABSOLUTE = os.path.join(PROJECT_ROOT_DIR, "logs")

_RX_LOGGING_INIT_ERROR = False
rx_log_file_path = ""

# --- 디코딩된 데이터 필드를 위한 헤더 정의 ---
# decoder.py의 반환 구조를 기반으로 평탄화된 필드 이름
DECODED_DATA_FIELDS = [
    "decoded_ts", # 기존 decoded_ts_valid, decoded_latency_ms와 구분하기 위해 'decoded_' 접두사 사용
    "decoded_accel_ax", "decoded_accel_ay", "decoded_accel_az",
    "decoded_gyro_gx", "decoded_gyro_gy", "decoded_gyro_gz",
    "decoded_angle_roll", "decoded_angle_pitch", "decoded_angle_yaw",
    "decoded_gps_lat", "decoded_gps_lon", "decoded_gps_altitude"
]
# --- 디코딩된 데이터 필드를 위한 헤더 정의 끝 ---

try:
    os.makedirs(LOG_DIR_ABSOLUTE, exist_ok=True)
    current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    rx_log_file_path = os.path.join(LOG_DIR_ABSOLUTE, f"rx_receiver_log_{current_date_str}.csv")

    BASE_RX_CSV_HEADER = [ # 디코딩된 데이터 필드를 제외한 기본 헤더
        "log_timestamp_utc",
        "event_type",
        "frame_seq_recv",
        "packet_type_recv_hex",
        "data_len_byte_value",
        "payload_len_on_wire",
        "rssi_dbm",
        "ack_seq_sent",
        "ack_type_sent_hex",
        "decoded_ts_valid_check", # 기존 decoded_ts_valid 이름 변경 (중복 방지)
        "decoded_latency_ms_calc",# 기존 decoded_latency_ms 이름 변경
        "notes"
    ]
    
    # 최종 CSV 헤더는 기본 헤더와 디코딩된 데이터 필드를 합침
    RX_CSV_HEADER = BASE_RX_CSV_HEADER + DECODED_DATA_FIELDS

    if not os.path.exists(rx_log_file_path) or os.path.getsize(rx_log_file_path) == 0:
        with open(rx_log_file_path, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(RX_CSV_HEADER)
except IOError as e:
    rx_internal_logger.error(f"수신 CSV 로그 파일 헤더 작성 실패 ({rx_log_file_path}): {e}")
    _RX_LOGGING_INIT_ERROR = True
except Exception as e:
    rx_internal_logger.error(f"수신 CSV 로그 파일 초기화 중 예기치 않은 오류: {e}", exc_info=True)
    _RX_LOGGING_INIT_ERROR = True


def log_rx_event(
    event_type: str,
    frame_seq_recv: Optional[int] = None,
    packet_type_recv_hex: Optional[str] = None,
    data_len_byte_value: Optional[int] = None,
    payload_len_on_wire: Optional[int] = None,
    rssi_dbm: Optional[int] = None,
    ack_seq_sent: Optional[int] = None,
    ack_type_sent_hex: Optional[str] = None,
    decoded_ts_valid: Optional[bool] = None, # receiver.py에서 계산된 값
    decoded_latency_ms: Optional[int] = None, # receiver.py에서 계산된 값
    decoded_payload_dict: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None
):
    if _RX_LOGGING_INIT_ERROR:
        return

    row_dict = {key: "" for key in RX_CSV_HEADER}
    
    try:
        log_ts_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds") + "Z"

        row_dict.update({
            "log_timestamp_utc": log_ts_utc_iso,
            "event_type": event_type,
            "frame_seq_recv": frame_seq_recv if frame_seq_recv is not None else '',
            "packet_type_recv_hex": packet_type_recv_hex if packet_type_recv_hex is not None else '',
            "data_len_byte_value": data_len_byte_value if data_len_byte_value is not None else '',
            "payload_len_on_wire": payload_len_on_wire if payload_len_on_wire is not None else '',
            "rssi_dbm": rssi_dbm if rssi_dbm is not None else '',
            "ack_seq_sent": ack_seq_sent if ack_seq_sent is not None else '',
            "ack_type_sent_hex": ack_type_sent_hex if ack_type_sent_hex is not None else '',
            "decoded_ts_valid_check": decoded_ts_valid if decoded_ts_valid is not None else '', # 이름 변경된 필드
            "decoded_latency_ms_calc": decoded_latency_ms if decoded_latency_ms is not None else '', # 이름 변경된 필드
            "notes": notes if notes is not None else '' # notes는 계속 유지 (간단한 메시지용)
        })

        # decoded_payload_dict가 있고, event_type이 DECODE_SUCCESS인 경우에만 필드 채우기
        if event_type == "DECODE_SUCCESS" and decoded_payload_dict:
            # 평탄화된 데이터 추출
            row_dict["decoded_ts"] = decoded_payload_dict.get("ts", "")
            
            accel = decoded_payload_dict.get("accel", {})
            row_dict["decoded_accel_ax"] = accel.get("ax", "")
            row_dict["decoded_accel_ay"] = accel.get("ay", "")
            row_dict["decoded_accel_az"] = accel.get("az", "")

            gyro = decoded_payload_dict.get("gyro", {})
            row_dict["decoded_gyro_gx"] = gyro.get("gx", "")
            row_dict["decoded_gyro_gy"] = gyro.get("gy", "")
            row_dict["decoded_gyro_gz"] = gyro.get("gz", "")

            angle = decoded_payload_dict.get("angle", {})
            row_dict["decoded_angle_roll"] = angle.get("roll", "")
            row_dict["decoded_angle_pitch"] = angle.get("pitch", "")
            row_dict["decoded_angle_yaw"] = angle.get("yaw", "")

            gps = decoded_payload_dict.get("gps", {})
            row_dict["decoded_gps_lat"] = gps.get("lat", "")
            row_dict["decoded_gps_lon"] = gps.get("lon", "")
            row_dict["decoded_gps_altitude"] = gps.get("altitude", "")
        
        row_list = [row_dict.get(header, '') for header in RX_CSV_HEADER]

        with open(rx_log_file_path, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row_list)
    except IOError as e:
        rx_internal_logger.error(f"수신 CSV 로그 기록 실패 ({rx_log_file_path}): {e} | 데이터: {row_dict}")
    except Exception as e:
        rx_internal_logger.error(f"수신 CSV 로그 기록 중 예기치 않은 오류 ({rx_log_file_path}): {e} | 데이터: {row_dict}", exc_info=True)