# ChirpChirp/source/transmitter/tx_logger.py
# -*- coding: utf-8 -*-

import csv
import os
import datetime
import logging
from typing import Optional

# --- 설정 (Configuration) ---
# tx_logger 모듈 내부 문제를 로깅하기 위한 로거
tx_internal_logger = logging.getLogger(__name__)

# 이 세션에서 사용할 로그 파일의 경로. 첫 번째 로그 기록 시점에 초기화됩니다.
_log_file_path: Optional[str] = None

# CSV 파일의 헤더
CSV_HEADER = [
    "log_timestamp_utc",        # 이 로그 항목이 기록된 UTC 시점
    "frame_seq",                # 전송되는 프레임의 순번
    "attempt_num_for_frame",    # 해당 프레임에 대한 현재 재시도 횟수
    "event_type",               # 발생한 이벤트 유형 (예: HANDSHAKE_SYN_SENT, DATA_ACK_OK)
    "total_attempts_for_frame", # (최종 결과) 해당 프레임의 총 시도 횟수
    "ack_received_final",       # (최종 결과) ACK 수신 성공 여부
    "timestamp_sent_utc",       # (전송 시) 패킷이 전송된 UTC 시점
    "timestamp_ack_interaction_end_utc" # (응답 시) ACK 관련 상호작용이 끝난 UTC 시점
]


def _initialize_session_log_file():
    """
    현재 전송 세션을 위한 새 로그 파일을 생성하고 헤더를 작성합니다.
    이 함수는 세션 당 한 번만 호출됩니다.
    """
    global _log_file_path
    try:
        # 1. 로그 디렉토리 경로 설정 ('tx_logs'로 변경)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root_dir = os.path.dirname(os.path.dirname(current_dir))
        log_dir_absolute = os.path.join(project_root_dir, "tx_logs")

        os.makedirs(log_dir_absolute, exist_ok=True)

        # 2. 세션 고유의 로그 파일 이름 생성 (예: tx_session_20231027_153000.csv)
        session_start_time_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(log_dir_absolute, f"tx_session_{session_start_time_str}.csv")

        # 3. 새 파일에 헤더 작성 (mode='w'로 새로 쓰기)
        with open(filepath, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
        
        # 성공적으로 파일 생성 후 전역 변수에 경로 할당
        _log_file_path = filepath
        tx_internal_logger.info(f"새로운 송신 로그 세션 시작. 파일: {_log_file_path}")

    except (IOError, OSError) as e:
        tx_internal_logger.error(f"로그 파일 초기화 실패: {e}")
        # 초기화 실패 시 _log_file_path는 None으로 유지되어, 이후 로깅 시도가 실패하게 됨
        _log_file_path = None


def log_tx_event(
    frame_seq: int,
    attempt_num: int, # 현재 프레임에 대한 시도 번호
    event_type: str,  # "SENT", "ACK_OK", "ACK_INVALID", "ACK_TIMEOUT" 등
    ts_sent: Optional[datetime.datetime] = None, # UTC datetime 객체
    ts_ack_interaction_end: Optional[datetime.datetime] = None, # UTC datetime 객체
    total_attempts_final: Optional[int] = None,
    ack_received_final: Optional[bool] = None
):
    """
    송신 관련 이벤트를 현재 세션의 CSV 로그 파일에 기록합니다.
    파일은 첫 이벤트 기록 시점에 자동으로 생성됩니다.
    모든 타임스탬프는 UTC를 기준으로 합니다.
    """
    global _log_file_path

    # 이 세션에서 처음으로 로그를 기록하는 경우, 로그 파일을 초기화
    if _log_file_path is None:
        _initialize_session_log_file()
    
    # 파일 초기화에 실패했거나 경로가 여전히 없는 경우, 로깅 중단
    if not _log_file_path:
        tx_internal_logger.warning(f"로그 파일이 준비되지 않아 이벤트 로그를 기록할 수 없습니다: (SEQ: {frame_seq}, EVT: {event_type})")
        return

    # row_dict를 먼저 기본값으로 초기화
    row_dict = {
        "log_timestamp_utc": "", "frame_seq": frame_seq, "attempt_num_for_frame": attempt_num,
        "event_type": event_type, "total_attempts_for_frame": "", "ack_received_final": "",
        "timestamp_sent_utc": "", "timestamp_ack_interaction_end_utc": ""
    }
    
    try:
        # 타임스탬프 포맷팅 (ISO 8601 형식)
        log_ts_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds") + "Z"
        ts_sent_utc_iso = ts_sent.isoformat(timespec="milliseconds") + "Z" if ts_sent else ''
        ts_ack_interaction_end_utc_iso = ts_ack_interaction_end.isoformat(timespec="milliseconds") + "Z" if ts_ack_interaction_end else ''

        # 전달받은 데이터로 row_dict 값 업데이트
        row_dict.update({
            "log_timestamp_utc": log_ts_utc_iso,
            "frame_seq": frame_seq,
            "attempt_num_for_frame": attempt_num,
            "event_type": event_type,
            "total_attempts_for_frame": total_attempts_final if total_attempts_final is not None else '',
            "ack_received_final": ack_received_final if ack_received_final is not None else '',
            "timestamp_sent_utc": ts_sent_utc_iso,
            "timestamp_ack_interaction_end_utc": ts_ack_interaction_end_utc_iso
        })
        
        # CSV_HEADER 순서에 맞춰 값을 리스트로 변환
        row_list = [row_dict.get(header, '') for header in CSV_HEADER]

        # 파일에 로그 추가 (mode='a'로 이어쓰기)
        with open(_log_file_path, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row_list)
            
    except (IOError, OSError) as e:
        tx_internal_logger.error(f"송신 로그 기록 실패 ({_log_file_path}): {e} | 데이터: {row_dict}")
    except Exception as e:
        tx_internal_logger.error(f"송신 로그 기록 중 예기치 않은 오류: {e} | 데이터: {row_dict}", exc_info=False)