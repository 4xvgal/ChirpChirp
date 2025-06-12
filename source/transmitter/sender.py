# sender.py
# -- coding: utf-8 --
from __future__ import annotations
import time
import logging
import serial
import struct
import datetime
from typing import Any, Dict, List, Optional, Tuple
import binascii

# --- [수정] 모듈 임포트 변경 ---
try:
    from .e22_config    import init_serial
    # from .packetizer    import make_frames  <- 삭제
    from .encoder       import encode         # <- 추가
    from .sensor_reader import SensorReader
    from .tx_logger     import log_tx_event
except ImportError:
    try:
        from e22_config    import init_serial
        # from packetizer    import make_frames  <- 삭제
        from encoder       import encode         # <- 추가
        from sensor_reader import SensorReader
        from tx_logger     import log_tx_event
    except ImportError as e:
        print(f"모듈 임포트 실패: {e}. 프로젝트 구조 및 PYTHONPATH를 확인하세요.")
        exit(1)
# --- [수정] 끝 ---


# --- [추가] 압축 방식 설정 ---
# "zlib", "none", "bam" 중 하나를 선택하세요.
COMPRESSION_METHOD = "none" 
# --- [추가] 끝 ---


# --- 상수 정의 (변경 없음) ---
GENERIC_TIMEOUT    = 5
SEND_COUNT         = 200
# ... (이하 기존 코드와 동일)
# 이하 생략 ... (기존 sender.py 코드 붙여넣기)

# send_data 함수 내부의 루프만 수정하면 됩니다.

def send_data(n: int = SEND_COUNT, mode: str = "reliable") -> int:
    # ... (상단 부분은 기존과 동일) ...

    current_message_seq_counter = 0
    print_separator(f"총 {n}회 데이터 전송 시작 (모드: {mode}, 압축: {COMPRESSION_METHOD})")

    for msg_idx in range(1, n + 1):
        print_separator(f"메시지 {msg_idx}/{n} (Message SEQ: {current_message_seq_counter}) 시작")
        sample = sr.get_sensor_data()

        if not sample or 'ts' not in sample:
            logger.warning(f"[메시지 {msg_idx}] 유효하지 않은 샘플 데이터 수신, 건너뜀. Sample: {sample}")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue
            
        # --- [핵심 수정] packetizer(make_frames) 대신 encoder.encode 직접 사용 ---
        # 1. encoder를 사용하여 페이로드 생성
        payload = encode(sample, method=COMPRESSION_METHOD)
        
        if not payload:
            logger.warning(f"[메시지 {msg_idx}] 페이로드 생성 실패 (아마도 encode 실패), 건너뜀")
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue
        
        # 2. 프레임 시퀀스 번호(ACK/Permit 비교용) 설정
        frame_seq_for_ack_handling = current_message_seq_counter
        
        # 3. 프레임 콘텐츠 생성 (SEQ 1바이트 + 페이로드)
        frame_content = bytes([frame_seq_for_ack_handling]) + payload
        frame_content_len = len(frame_content)
        
        # 4. 최종 전송 패킷 생성 (LENGTH 1바이트 + 프레임 콘텐츠)
        raw_data_packet = bytes([frame_content_len]) + frame_content
        # --- [핵심 수정] 끝 ---

        logger.info(f"[메시지 {msg_idx}] 생성된 페이로드 길이={len(payload)}B, 프레임 콘텐츠 길이(LENGTH 값)={frame_content_len}B")

        if mode == "PDR":
            pdr_messages_tx_initiated_count += 1
            
        # 이하 로직은 raw_data_packet, frame_seq_for_ack_handling 변수를 그대로 사용하므로 변경할 필요 없음
        
        query_attempts = 0
        permission_received = False
        # ... (이하 send_data 함수의 나머지 코드는 기존과 동일) ...