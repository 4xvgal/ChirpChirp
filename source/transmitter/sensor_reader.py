# ChirpChirp/source/transmitter/tx_logger.py
# -*- coding: utf-8 -*-

import csv
import os
import datetime
import logging
import binascii  # 페이로드를 Hex로 변환하기 위해 추가
from typing import Optional

# --- 설정 (Configuration) ---
tx_internal_logger = logging.getLogger(__name__)

_log_file_path: Optional[str] = None

# CSV 파일 헤더에 'payload_hex' 추가
CSV_HEADER = [
    "log_timestamp_utc",        # 이 로그 항목이 기록된 UTC 시점
    "frame_seq",                # 전송되는 프레임의 순번
    "attempt_num_for_frame",    # 해당 프레임에 대한 현재 재시도 횟수
    "event_type",               # 발생한 이벤트 유형 (예: HANDSHAKE_SYN_SENT, DATA_ACK_OK)
    "total_attempts_for_frame", # (최종 결과) 해당 프레임의 총 시도 횟수
    "ack_received_final",       # (최종 결과) ACK 수신 성공 여부
    "payload_hex",              # (전송 시) 전송된 실제 페이로드/프레임 (Hex 형식)
    "timestamp_sent_utc",       # (전송 시) 패킷이 전송된 UTC 시점
    "timestamp_ack_interaction_end_utc" # (응답 시) ACK 관련 상호작용이 끝난 UTC 시점
]


def start_new_log_session():
    """
    새로운 측정 세션을 시작하고, 새 로그 파일을 생성합니다.
    """
    global _log_file_path
    _log_file_path = None 
    _initialize_session_log_file()


def _initialize_session_log_file():
    """
    현재 전송 세션을 위한 새 로그 파일을 생성하고 헤더를 작성합니다.
    """
    global _log_file_path
    if _log_file_path is not None:
        return

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root_dir = os.path.dirname(os.path.dirname(current_dir))
        log_dir_absolute = os.path.join(project_root_dir, "tx_logs")

        os.makedirs(log_dir_absolute, exist_ok=True)

        session_start_time_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(log_dir_absolute, f"tx_session_{session_start_time_str}.csv")

        with open(filepath, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
        
        _log_file_path = filepath
        tx_internal_logger.info(f"새로운 송신 로그 세션 시작. 파일: {_log_file_path}")

    except (IOError, OSError) as e:
        tx_internal_logger.error(f"로그 파일 초기화 실패: {e}")
        _log_file_path = None


def log_tx_event(
    frame_seq: int,
    attempt_num: int,
    event_type: str,
    ts_sent: Optional[datetime.datetime] = None,
    ts_ack_interaction_end: Optional[datetime.datetime] = None,
    total_attempts_final: Optional[int] = None,
    ack_received_final: Optional[bool] = None,
    payload: Optional[bytes] = None  # payload를 인자로 추가
):
    """
    송신 관련 이벤트를 현재 세션의 CSV 로그 파일에 기록합니다.
    """
    global _log_file_path
    
    if not _log_file_path:
        tx_internal_logger.warning(f"로그 파일이 준비되지 않아 이벤트 로그를 기록할 수 없습니다. (SEQ: {frame_seq}, EVT: {event_type})")
        return

    # row_dict에 payload_hex 필드 추가
    row_dict = {
        "log_timestamp_utc": "", "frame_seq": frame_seq, "attempt_num_for_frame": attempt_num,
        "event_type": event_type, "total_attempts_for_frame": "", "ack_received_final": "",
        "payload_hex": "", "timestamp_sent_utc": "", "timestamp_ack_interaction_end_utc": ""
    }
    
    try:
        # 타임스탬프 포맷팅
        log_ts_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds") + "Z"
        ts_sent_utc_iso = ts_sent.isoformat(timespec="milliseconds") + "Z" if ts_sent else ''
        ts_ack_interaction_end_utc_iso = ts_ack_interaction_end.isoformat(timespec="milliseconds") + "Z" if ts_ack_interaction_end else ''
        
        # 페이로드를 hex 문자열로 변환
        payload_hex_str = binascii.hexlify(payload).decode('ascii') if payload else ''

        # row_dict 업데이트
        row_dict.update({
            "log_timestamp_utc": log_ts_utc_iso,
            "frame_seq": frame_seq,
            "attempt_num_for_frame": attempt_num,
            "event_type": event_type,
            "total_attempts_for_frame": total_attempts_final if total_attempts_final is not None else '',
            "ack_received_final": ack_received_final if ack_received_final is not None else '',
            "payload_hex": payload_hex_str,
            "timestamp_sent_utc": ts_sent_utc_iso,
            "timestamp_ack_interaction_end_utc": ts_ack_interaction_end_utc_iso
        })
        
        # CSV 파일에 쓰기
        row_list = [row_dict.get(header, '') for header in CSV_HEADER]
        with open(_log_file_path, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row_list)
            
    except (IOError, OSError) as e:
        tx_internal_logger.error(f"송신 로그 기록 실패 ({_log_file_path}): {e} | 데이터: {row_dict}")
    except Exception as e:
        tx_internal_logger.error(f"송신 로그 기록 중 예기치 않은 오류: {e} | 데이터: {row_dict}", exc_info=False)
