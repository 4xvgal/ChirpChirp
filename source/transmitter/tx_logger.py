# ChirpChirp/source/transmitter/tx_logger.py
# -*- coding: utf-8 -*-

import csv
import os
import datetime
import logging
from typing import Optional, Union

# 로거 설정 (tx_logger 내부 문제 로깅용)
tx_internal_logger = logging.getLogger(__name__) # tx_logger 모듈 내부용 로거


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR)) # ChirpChirp/
LOG_DIR_ABSOLUTE = os.path.join(PROJECT_ROOT_DIR, "logs")

os.makedirs(LOG_DIR_ABSOLUTE, exist_ok=True) # 로그 디렉토리 생성

# 현재 날짜를 기반으로 로그 파일 이름 생성
current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
# 로그 파일명에 "tx_sender" 등을 넣어 송신측 로그임을 명시
log_file_path = os.path.join(LOG_DIR_ABSOLUTE, f"tx_sender_log_{current_date_str}.csv")


CSV_HEADER = [
    "log_timestamp_utc",        # 이 로그 항목이 기록된 UTC 시점
    "packet_id",
    "frame_seq",
    "attempt_num_for_frame",
    "event_type",
    "total_attempts_for_frame", # 최종 결과 시
    "ack_received_final",       # 최종 결과 시
    "timestamp_sent_utc",       # SENT 이벤트 시
    "timestamp_ack_interaction_end_utc" # ACK_OK, ACK_INVALID, ACK_TIMEOUT 이벤트 시
]

# 파일이 없거나 비어있으면 헤더 작성
if not os.path.exists(log_file_path) or os.path.getsize(log_file_path) == 0:
    try:
        with open(log_file_path, mode='w', newline='', encoding='utf-8') as f: # mode 'w'로 변경하여 새로 만들거나 덮어씀
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
    except IOError as e:
        tx_internal_logger.error(f"송신 로그 파일 헤더 작성 실패 ({log_file_path}): {e}")

def log_tx_event(
    pkt_id: int,
    frame_seq: int,
    attempt_num: int, # 현재 프레임에 대한 시도 번호
    event_type: str,  # "SENT", "ACK_OK", "ACK_INVALID", "ACK_TIMEOUT", "TX_FAIL"
    ts_sent: Optional[datetime.datetime] = None, # UTC datetime 객체
    ts_ack_interaction_end: Optional[datetime.datetime] = None, # UTC datetime 객체
    total_attempts_final: Optional[int] = None,
    ack_received_final: Optional[bool] = None
):
    """
    송신 관련 이벤트를 CSV 파일에 로깅합니다.
    모든 타임스탬프는 UTC를 기준으로 합니다.
    """
    try:
        log_ts_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        ts_sent_utc_iso = ts_sent.isoformat() if ts_sent else ''
        ts_ack_interaction_end_utc_iso = ts_ack_interaction_end.isoformat() if ts_ack_interaction_end else ''

        row_dict = {
            "log_timestamp_utc": log_ts_utc_iso,
            "packet_id": pkt_id,
            "frame_seq": frame_seq,
            "attempt_num_for_frame": attempt_num,
            "event_type": event_type,
            "total_attempts_for_frame": total_attempts_final if total_attempts_final is not None else '',
            "ack_received_final": ack_received_final if ack_received_final is not None else '',
            "timestamp_sent_utc": ts_sent_utc_iso,
            "timestamp_ack_interaction_end_utc": ts_ack_interaction_end_utc_iso
        }
        
        # CSV_HEADER 순서대로 값을 가져와 리스트로 만듦
        row_list = [row_dict.get(header, '') for header in CSV_HEADER]

        with open(log_file_path, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row_list)
    except IOError as e:
        tx_internal_logger.error(f"송신 로그 기록 실패 ({log_file_path}): {e} | 데이터: {row_dict}")
    except Exception as e:
        tx_internal_logger.error(f"송신 로그 기록 중 예기치 않은 오류 ({log_file_path}): {e} | 데이터: {row_dict}")

