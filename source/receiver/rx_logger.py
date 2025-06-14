# ChirpChirp/source/receiver/rx_logger.py
# -*- coding: utf-8 -*-

import csv
import os
import datetime
import logging
from typing import Optional, Dict, Any
from pathlib import Path

rx_internal_logger = logging.getLogger(__name__)

# --- 설정 및 경로 ---
_RX_LOGGING_INIT_ERROR = False
rx_log_file_path = ""

try:
    PROJECT_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
    LOG_DIR_ABSOLUTE = PROJECT_ROOT_DIR / "logs"
    LOG_DIR_ABSOLUTE.mkdir(exist_ok=True)
    
    current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    rx_log_file_path = LOG_DIR_ABSOLUTE / f"rx_receiver_log_{current_date_str}.csv"

    # --- CSV 헤더 정의 ---
    # 1. 기본 이벤트 정보 헤더
    BASE_EVENT_HEADER = [
        "log_timestamp_utc", "event_type", "frame_seq_recv", "rssi_dbm", "notes"
    ]
    
    # 2. 수신된 패킷 자체에 대한 정보 헤더
    RAW_PACKET_HEADER = [
        "packet_type_recv_hex", "data_len_byte_value", "payload_len_on_wire"
    ]

    # 3. 송신한 ACK에 대한 정보 헤더
    ACK_RESPONSE_HEADER = ["ack_seq_sent", "ack_type_sent_hex"]

    # 4. 디코딩 성공 시 계산되는 메타 정보 헤더
    DECODE_META_HEADER = ["is_decoded_ts_valid", "calculated_latency_ms"]

    # 5. 실제 디코딩된 센서 데이터 헤더 (평탄화)
    DECODED_DATA_FIELDS_HEADER = [
        "decoded_ts",
        "decoded_accel_ax", "decoded_accel_ay", "decoded_accel_az",
        "decoded_gyro_gx", "decoded_gyro_gy", "decoded_gyro_gz",
        "decoded_angle_roll", "decoded_angle_pitch", "decoded_angle_yaw",
        "decoded_gps_lat", "decoded_gps_lon", "decoded_gps_altitude"
    ]

    # 모든 헤더를 순서대로 조합
    RX_CSV_HEADER = (
        BASE_EVENT_HEADER + RAW_PACKET_HEADER + ACK_RESPONSE_HEADER + 
        DECODE_META_HEADER + DECODED_DATA_FIELDS_HEADER
    )

    if not rx_log_file_path.is_file() or rx_log_file_path.stat().st_size == 0:
        with open(rx_log_file_path, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(RX_CSV_HEADER)

except (IOError, Exception) as e:
    rx_internal_logger.error(f"수신 CSV 로그 파일 초기화 실패: {e}", exc_info=True)
    _RX_LOGGING_INIT_ERROR = True

def _flatten_decoded_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """디코딩된 중첩 딕셔너리를 CSV에 기록하기 위해 평탄화합니다."""
    flat_data = {}
    if not data:
        return flat_data

    flat_data["decoded_ts"] = data.get("ts", "")
    
    accel = data.get("accel", {})
    flat_data["decoded_accel_ax"] = accel.get("ax", "")
    flat_data["decoded_accel_ay"] = accel.get("ay", "")
    flat_data["decoded_accel_az"] = accel.get("az", "")

    gyro = data.get("gyro", {})
    flat_data["decoded_gyro_gx"] = gyro.get("gx", "")
    flat_data["decoded_gyro_gy"] = gyro.get("gy", "")
    flat_data["decoded_gyro_gz"] = gyro.get("gz", "")

    angle = data.get("angle", {})
    flat_data["decoded_angle_roll"] = angle.get("roll", "")
    flat_data["decoded_angle_pitch"] = angle.get("pitch", "")
    flat_data["decoded_angle_yaw"] = angle.get("yaw", "")

    gps = data.get("gps", {})
    flat_data["decoded_gps_lat"] = gps.get("lat", "")
    flat_data["decoded_gps_lon"] = gps.get("lon", "")
    flat_data["decoded_gps_altitude"] = gps.get("altitude", "")
    
    return flat_data

def log_rx_event(
    event_type: str,
    frame_seq_recv: Optional[int] = None,
    rssi_dbm: Optional[int] = None,
    packet_type_recv_hex: Optional[str] = None,
    data_len_byte_value: Optional[int] = None,
    payload_len_on_wire: Optional[int] = None,
    ack_seq_sent: Optional[int] = None,
    ack_type_sent_hex: Optional[str] = None,
    is_decoded_ts_valid: Optional[bool] = None,
    calculated_latency_ms: Optional[int] = None,
    decoded_payload_dict: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None
):
    """수신 관련 이벤트를 CSV 파일에 로깅합니다."""
    if _RX_LOGGING_INIT_ERROR:
        return

    # 모든 필드를 빈 문자열로 초기화
    row_dict = {key: "" for key in RX_CSV_HEADER}
    
    try:
        # 1. 기본 정보 업데이트
        row_dict.update({
            "log_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds") + "Z",
            "event_type": event_type,
            "frame_seq_recv": frame_seq_recv,
            "rssi_dbm": rssi_dbm,
            "notes": notes
        })

        # 2. 선택적 정보 업데이트
        if packet_type_recv_hex is not None: row_dict["packet_type_recv_hex"] = packet_type_recv_hex
        if data_len_byte_value is not None: row_dict["data_len_byte_value"] = data_len_byte_value
        if payload_len_on_wire is not None: row_dict["payload_len_on_wire"] = payload_len_on_wire
        if ack_seq_sent is not None: row_dict["ack_seq_sent"] = ack_seq_sent
        if ack_type_sent_hex is not None: row_dict["ack_type_sent_hex"] = ack_type_sent_hex
        if is_decoded_ts_valid is not None: row_dict["is_decoded_ts_valid"] = is_decoded_ts_valid
        if calculated_latency_ms is not None: row_dict["calculated_latency_ms"] = calculated_latency_ms
        
        # 3. 디코딩된 데이터 평탄화 및 업데이트 (DECODE_SUCCESS 이벤트에만 해당)
        if event_type == "DECODE_SUCCESS" and decoded_payload_dict:
            flat_data = _flatten_decoded_data(decoded_payload_dict)
            row_dict.update(flat_data)
        
        # 4. 최종적으로 CSV에 기록
        row_list = [row_dict.get(header, '') for header in RX_CSV_HEADER]
        with open(rx_log_file_path, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row_list)
            
    except (IOError, Exception) as e:
        rx_internal_logger.error(f"수신 CSV 로그 기록 실패: {e} | 데이터: {row_dict}", exc_info=True)