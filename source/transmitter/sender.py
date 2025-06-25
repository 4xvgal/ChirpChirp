# sender.py
# -- coding: utf-8 --
from __future__ import annotations
import time
import logging
import serial
import struct
import datetime
import sys
import binascii
from typing import Any, Dict, List, Optional, Tuple

try:
    from .e22_config import init_serial
    from .encoder import create_frame
    from .sensor_reader import SensorReader
    from .tx_logger import log_tx_event, start_new_log_session
except ImportError:
    try:
        from e22_config import init_serial
        from encoder import create_frame
        from sensor_reader import SensorReader
        from tx_logger import log_tx_event, start_new_log_session
    except ImportError as e:
        print(f"모듈 임포트 실패: {e}. 프로젝트 구조 및 PYTHONPATH를 확인하세요.")
        exit(1)

# --- 설정 (Configuration) ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


SEND_COUNT         = 100
GENERIC_TIMEOUT    = 10
RETRY_HANDSHAKE    = 10
RETRY_QUERY_PERMIT = 50
RETRY_DATA_ACK     = 50
SYN_MSG            = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA      = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT  = 0x55
ACK_PACKET_LEN     = 2
HANDSHAKE_ACK_SEQ  = 0x00

# --- Helper Functions (변경 없음) ---
def print_separator(title: str, length: int = 60, char: str = '-') -> None:
    if len(title) + 2 > length: logger.info(f"-- {title} --")
    else:
        pad = (length - len(title) - 2) // 2
        line = char * pad + f" {title} " + char * pad
        if len(line) < length: line += char
        logger.info(line)

def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        s.timeout = GENERIC_TIMEOUT; s.inter_byte_timeout = None; time.sleep(0.1)
        return s
    except serial.SerialException as e:
        logger.error(f"시리얼 포트 열기 실패: {e}"); raise

def bytes_to_hex_pretty_str(data_bytes: bytes, bytes_per_line: int = 16) -> str:
    if not data_bytes: return "<empty>"
    hex_str = binascii.hexlify(data_bytes).decode('ascii')
    lines: List[str] = [f"  {' '.join(hex_str[i:i+2] for i in range(0, len(line), 2))}" for line in [hex_str[j:j+bytes_per_line*2] for j in range(0, len(hex_str), bytes_per_line*2)]]
    return "\n".join(lines)

def _tx_data_packet(s: serial.Serial, buf: bytes) -> Tuple[bool, Optional[datetime.datetime]]:
    ts_sent = datetime.datetime.now(datetime.timezone.utc)
    try:
        written = s.write(buf); s.flush()
        if logger.isEnabledFor(logging.DEBUG): logger.debug(f"DATA PKT TX ({len(buf)}B):\n{bytes_to_hex_pretty_str(buf)}")
        else: logger.info(f"DATA PKT TX ({len(buf)}B)")
        return written == len(buf), ts_sent
    except Exception as e:
        logger.error(f"DATA PKT TX 실패: {e}"); return False, ts_sent

def _tx_control_packet(s: serial.Serial, seq: int, packet_type: int) -> bool:
    pkt_bytes = struct.pack("!BB", packet_type, seq)
    try:
        written = s.write(pkt_bytes); s.flush()
        type_name = {QUERY_TYPE_SEND_REQUEST: "QUERY_SEND_REQUEST"}.get(packet_type, f"UNKNOWN_0x{packet_type:02x}")
        if logger.isEnabledFor(logging.DEBUG): logger.debug(f"CTRL PKT TX ({len(pkt_bytes)}B): TYPE={type_name}, SEQ={seq}\n{bytes_to_hex_pretty_str(pkt_bytes)}")
        else: logger.info(f"CTRL PKT TX: TYPE={type_name}, SEQ={seq}")
        return written == len(pkt_bytes)
    except Exception as e:
        logger.error(f"CTRL PKT TX 실패 (TYPE=0x{packet_type:02x}, SEQ={seq}): {e}"); return False

# --- ★★★★★ 핸드셰이크 로깅 수정 ★★★★★ ---
def _handshake(s: serial.Serial) -> bool:
    print_separator("핸드셰이크 시작")
    s.timeout = GENERIC_TIMEOUT
    for attempt in range(1, RETRY_HANDSHAKE + 1):
        logger.info(f"[핸드셰이크] SYN 전송 ({attempt}/{RETRY_HANDSHAKE})")
        sent_ok, ts_syn_sent = _tx_data_packet(s, SYN_MSG)
        log_tx_event(frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_SYN_SENT' if sent_ok else 'HANDSHAKE_SYN_FAIL', ts_sent=ts_syn_sent, payload=SYN_MSG)
        if not sent_ok:
            if attempt < RETRY_HANDSHAKE: time.sleep(1)
            continue
        
        logger.info(f"[핸드셰이크] ACK 대기 중 (Timeout: {s.timeout}s)...")
        ack_bytes = s.read(ACK_PACKET_LEN)
        ts_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)
        if len(ack_bytes) == ACK_PACKET_LEN:
            try:
                atype, seq = struct.unpack("!BB", ack_bytes)
                if atype == ACK_TYPE_HANDSHAKE and seq == HANDSHAKE_ACK_SEQ:
                    logger.info("[핸드셰이크] 성공"); print_separator("핸드셰이크 완료")
                    log_tx_event(frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_OK', ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end, total_attempts_final=attempt, ack_received_final=True, payload=SYN_MSG)
                    return True
                else:
                    log_tx_event(frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_INVALID', ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end, payload=SYN_MSG)
            except struct.error:
                log_tx_event(frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_UNPACK_FAIL', ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end, payload=SYN_MSG)
        else:
            log_tx_event(frame_seq=HANDSHAKE_ACK_SEQ, attempt_num=attempt, event_type='HANDSHAKE_ACK_TIMEOUT', ts_sent=ts_syn_sent, ts_ack_interaction_end=ts_ack_interaction_end, payload=SYN_MSG)
        
        if attempt < RETRY_HANDSHAKE: time.sleep(1)

    logger.error("[핸드셰이크] 최종 실패"); print_separator("핸드셰이크 실패")
    return False

# --- ★★★★★ 데이터 전송 로깅 수정 ★★★★★ ---
def send_data(n: int, mode: str, compression_mode: str, payload_size: int) -> int:
    logger.info("새로운 전송 세션을 시작하며, 로그 파일을 생성합니다."); start_new_log_session()
    
    try: s = _open_serial()
    except Exception: return -1
    
    if not _handshake(s): s.close(); return 0

    s.timeout = GENERIC_TIMEOUT; s.inter_byte_timeout = 0.1

    if mode == "PDR": effective_retry_query_permit, effective_retry_data_ack = 1, 1; logger.info("PDR 측정 모드. 재전송 비활성화.")
    elif mode == "reliable": effective_retry_query_permit, effective_retry_data_ack = RETRY_QUERY_PERMIT, RETRY_DATA_ACK; logger.info("신뢰성 전송 모드. 재전송 활성화.")
    else: logger.error(f"알 수 없는 모드: {mode}. 'reliable' 또는 'PDR' 사용."); s.close(); return -2

    sr = None
    if payload_size == 0:
        try: sr = SensorReader()
        except Exception as e: logger.critical(f"SensorReader 초기화 실패: {e}"); s.close(); return -3

    reliable_ok_count, pdr_data_acks_received_count, pdr_messages_tx_initiated_count, current_message_seq_counter = 0, 0, 0, 0
    
    payload_log_str = "Sensor Data" if payload_size == 0 else f"Dummy Data ({payload_size}B)"
    logger.info(f"사용될 인코딩 모드: '{compression_mode}', 페이로드: '{payload_log_str}'")
    print_separator(f"총 {n}회 데이터 전송 시작 (모드: {mode})")

    for msg_idx in range(1, n + 1):
        print_separator(f"메시지 {msg_idx}/{n} (Message SEQ: {current_message_seq_counter}) 시작")
        
        sample = sr.get_sensor_data() if payload_size == 0 and sr else {}
        if payload_size == 0 and (not sample or 'ts' not in sample):
            logger.warning(f"[메시지 {msg_idx}] 유효하지 않은 샘플, 건너뜀.")
            # 건너뛴 메시지도 로그에 남기기
            log_tx_event(
                frame_seq=current_message_seq_counter,
                attempt_num=0,
                event_type='SKIP_INVALID_SAMPLE',
                ts_sent=None,
                total_attempts_final=0,
                ack_received_final=False
            )
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue

        frame_content = create_frame(sample, current_message_seq_counter, compression_mode, payload_size)
        if not frame_content:
            logger.warning(f"[메시지 {msg_idx}] 프레임 생성 실패, 건너뜀")
            # 프레임 생성 실패도 로그에 남기기
            log_tx_event(
                frame_seq=current_message_seq_counter,
                attempt_num=0,
                event_type='SKIP_FRAME_CREATION_FAIL',
                ts_sent=None,
                total_attempts_final=0,
                ack_received_final=False
            )
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue
        
        if mode == "PDR": pdr_messages_tx_initiated_count += 1
        raw_data_packet = bytes([len(frame_content)]) + frame_content
        frame_seq_for_ack_handling = frame_content[0]

        # --- Query/Permit (여기는 로깅 생략, 필요 시 추가 가능) ---
        query_attempts, permission_received = 0, False
        while not permission_received and query_attempts < effective_retry_query_permit:
            query_attempts += 1
            if not _tx_control_packet(s, frame_seq_for_ack_handling, QUERY_TYPE_SEND_REQUEST):
                if query_attempts < effective_retry_query_permit: time.sleep(0.5); continue
                else: break
            permit_ack_bytes = s.read(ACK_PACKET_LEN)
            if len(permit_ack_bytes) == ACK_PACKET_LEN and struct.unpack("!BB", permit_ack_bytes) == (ACK_TYPE_SEND_PERMIT, frame_seq_for_ack_handling): permission_received = True
            if not permission_received and query_attempts < effective_retry_query_permit: time.sleep(1)
        if not permission_received:
            logger.error(f"[메시지 {msg_idx}] 최종 Permit 미수신. 메시지 실패 처리.")
            # Permit 실패도 하나의 시도이니 로그에 남기기
            log_tx_event(
                frame_seq=frame_seq_for_ack_handling,
                attempt_num=query_attempts,
                event_type='PERMIT_FINAL_FAILURE',
                ts_sent=None,
                total_attempts_final=query_attempts,
                ack_received_final=False
            )
            current_message_seq_counter = (current_message_seq_counter + 1) % 256
            time.sleep(1)
            continue
        
        # --- 데이터 전송 및 ACK 확인 (상세 로깅) ---
        data_tx_attempts, data_ack_received = 0, False
        ts_sent_for_attempt = None
        while not data_ack_received and data_tx_attempts < effective_retry_data_ack:
            data_tx_attempts += 1
            
            # 데이터 전송 시도 로깅
            sent_ok, ts_sent_for_attempt = _tx_data_packet(s, raw_data_packet)
            log_tx_event(frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_SENT' if sent_ok else 'DATA_SEND_FAIL', ts_sent=ts_sent_for_attempt, payload=raw_data_packet)
            if not sent_ok:
                if data_tx_attempts < effective_retry_data_ack: time.sleep(1); continue
                else: break

            # ACK 수신 결과 로깅
            data_ack_bytes = s.read(ACK_PACKET_LEN)
            ts_ack_interaction_end = datetime.datetime.now(datetime.timezone.utc)

            if len(data_ack_bytes) == ACK_PACKET_LEN:
                try:
                    ack_type, ack_seq = struct.unpack("!BB", data_ack_bytes)
                    if ack_type == ACK_TYPE_DATA and ack_seq == frame_seq_for_ack_handling:
                        data_ack_received = True
                        if mode == "PDR": pdr_data_acks_received_count += 1
                        log_tx_event(frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_OK', ts_sent=ts_sent_for_attempt, ts_ack_interaction_end=ts_ack_interaction_end, total_attempts_final=data_tx_attempts, ack_received_final=True, payload=raw_data_packet)
                    else:
                        log_tx_event(frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_INVALID', ts_sent=ts_sent_for_attempt, ts_ack_interaction_end=ts_ack_interaction_end, payload=raw_data_packet)
                except struct.error:
                    log_tx_event(frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_UNPACK_FAIL', ts_sent=ts_sent_for_attempt, ts_ack_interaction_end=ts_ack_interaction_end, payload=raw_data_packet)
            else:
                log_tx_event(frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_ACK_TIMEOUT', ts_sent=ts_sent_for_attempt, ts_ack_interaction_end=ts_ack_interaction_end, payload=raw_data_packet)
            
            if not data_ack_received and data_tx_attempts < effective_retry_data_ack: time.sleep(1)

        # 최종 결과 처리
        if data_ack_received:
            if mode == "reliable": reliable_ok_count += 1
            logger.info(f"[메시지 {msg_idx}] 전송 완료 ({msg_idx}/{n})")
        else:
            logger.error(f"[메시지 {msg_idx}] 최종 데이터 ACK 미수신. 메시지 실패 처리.")
            # 최종 실패에 대한 명시적 로그 추가
            log_tx_event(frame_seq=frame_seq_for_ack_handling, attempt_num=data_tx_attempts, event_type='DATA_FINAL_FAILURE', ts_sent=ts_sent_for_attempt, ts_ack_interaction_end=datetime.datetime.now(datetime.timezone.utc), total_attempts_final=data_tx_attempts, ack_received_final=False, payload=raw_data_packet)

        current_message_seq_counter = (current_message_seq_counter + 1) % 256
        time.sleep(1)

    # --- 최종 결과 출력 (변경 없음) ---
    final_return_value: int
    if mode == "PDR":
        pdr = (pdr_data_acks_received_count / n) if n > 0 else 0.0
        logger.info(f"PDR Mode 결과: {pdr_data_acks_received_count}/{n} ({pdr:.2%}) 성공"); final_return_value = pdr_data_acks_received_count
    else: logger.info(f"신뢰성 전송 완료: {reliable_ok_count}/{n} 메시지 성공"); final_return_value = reliable_ok_count

    if s and s.is_open: s.close()
    return final_return_value

# --- Main 실행 블록 (변경 없음) ---
if __name__ == '__main__':
    logging.getLogger().setLevel(logging.INFO)
    if len(sys.argv) != 3: print("사용법: python sender.py <mode> <payload_size>\n  <mode>: raw, bam\n  <payload_size>: 0, 8, 16, 24, 32"); sys.exit(1)
    comp_mode_arg = sys.argv[1].lower()
    if comp_mode_arg not in ['raw', 'bam']: print(f"오류: 잘못된 모드 '{comp_mode_arg}'. 'raw' 또는 'bam' 사용."); sys.exit(1)
    try: payload_size_arg = int(sys.argv[2]); assert payload_size_arg in [0, 8, 16, 24, 32]
    except (ValueError, AssertionError): print(f"오류: 잘못된 payload_size '{sys.argv[2]}'. 0, 8, 16, 24, 32 중 하나 사용."); sys.exit(1)

    payload_str = "Sensor Data" if payload_size_arg == 0 else f"Dummy {payload_size_arg}B"
    logger.info(f"\n{'='*10} PDR 모드 테스트 시작 (Mode: {comp_mode_arg}, Payload: {payload_str}) {'='*10}")
    pdr_acks_received = send_data(n=SEND_COUNT, mode="PDR", compression_mode=comp_mode_arg, payload_size=payload_size_arg)
    logger.info(f"PDR 모드 테스트 종료, 수신된 데이터 ACK 총계: {pdr_acks_received}\n{'='*40}")