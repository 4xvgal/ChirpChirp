# sender.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import time, logging, serial
from typing import Any, Dict, List

# e22_config, packetizer, sensor_reader는 올바르게 임포트된다고 가정
try:
    from e22_config    import init_serial
    from packetizer    import make_frames
    from sensor_reader import SensorReader
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 필요한 파일들이 올바른 위치에 있는지 확인하세요.")
    exit(1)


# ────────── 설정 ──────────
MAX_PAYLOAD       = 56
FRAME_MAX         = 2 + MAX_PAYLOAD # LEN 바이트 제외한 프레임의 최대 길이 (SEQ+TOTAL+PAYLOAD_CHUNK)
HANDSHAKE_TIMEOUT = 5.0 # 수신기와 유사하게, 또는 약간 더 길게 설정 권장
SEND_COUNT        = 1000
RETRY_HANDSHAKE   = 5
DELAY_BETWEEN     = 0.3

# 수신기가 b"SYN\n"을 기대하므로, 여기에 맞춥니다.
SYN = b"SYN\n"
ACK = b"ACK\n"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        s.timeout = HANDSHAKE_TIMEOUT # 핸드셰이크용 타임아웃 설정
        time.sleep(0.1) # 모듈 안정화 시간
        return s
    except serial.SerialException as e:
        logging.error(f"시리얼 포트 열기 실패 (e22_config.init_serial): {e}")
        raise # 예외를 다시 발생시켜 프로그램이 비정상 종료되도록 함


def _tx(s: serial.Serial, buf: bytes) -> bool:
    try:
        written = s.write(buf)
        s.flush() # 버퍼를 비워 즉시 전송
        logging.debug(f"TX: {buf!r}") # 전송 내용 로깅 (디버그 레벨)
        return written == len(buf)
    except Exception as e:
        logging.error(f"TX 실패: {e}")
        return False


def _handshake(s: serial.Serial) -> bool:
    for i in range(RETRY_HANDSHAKE):
        logging.info(f"핸드셰이크 시도 {i+1}/{RETRY_HANDSHAKE} - SYN 전송: {SYN!r}")
        if not _tx(s, SYN): # _tx 함수를 사용하여 SYN 전송
            logging.error(f"SYN 전송 실패 (시도 {i+1})")
            time.sleep(1) # 실패 시 잠시 대기 후 재시도
            continue
        
        # ACK 수신 로직: readline()은 \n을 만날 때까지 읽음
        # s.timeout은 HANDSHAKE_TIMEOUT으로 설정되어 있음
        received_line = s.readline() 
        
        if received_line:
            logging.info(f"ACK 수신 시도: 받은 데이터: {received_line!r} (기대: {ACK!r})")
            if received_line == ACK: # 정확히 ACK\n 인지 비교
                logging.info("핸드셰이크 성공 (ACK 수신)")
                return True
            else:
                logging.warning(f"수신된 ACK 불일치: {received_line!r} vs {ACK!r}")
        else:
            # 타임아웃 발생 (readline()이 빈 바이트 문자열 반환)
            logging.warning(f"ACK 수신 타임아웃 (시도 {i+1}/{RETRY_HANDSHAKE})")
            
    logging.error("핸드셰이크 최종 실패")
    return False


def send_data(n: int = SEND_COUNT) -> int:
    try:
        s = _open_serial()
    except Exception: # _open_serial에서 예외 발생 시
        logging.error("시리얼 포트 초기화 실패, 종료합니다.")
        return 0
        
    if not _handshake(s):
        logging.error("핸드셰이크 최종 실패, 종료합니다.")
        s.close()
        return 0

    s.timeout = 0.1 # 데이터 전송용 타임아웃 (짧게)
    sr = SensorReader()
    ok = 0

    logging.info(f"--- {n}회 데이터 전송 시작 ---")
    for i in range(1, n + 1):
        logging.debug(f"[{i}/{n}] 센서 데이터 읽기 시도...")
        sample = sr.get_sensor_data()
        if not sample or not all(k in sample for k in ["ts", "accel", "gyro", "angle", "gps"]): # 주요 키 존재 확인
            logging.warning(f"[{i}/{n}] 불완전 샘플 또는 빈 샘플, 건너뜀: {sample!r}")
            time.sleep(1)
            continue
        
        logging.debug(f"[{i}/{n}] 프레임 생성 시도...")
        frames = make_frames(sample) # packetizer.py 호출
        if not frames:
            # make_frames가 빈 리스트를 반환하는 경우는 packetizer.py 로직에 따라 다름
            # (예: 압축 후 데이터가 너무 작거나 없을 때 등)
            logging.warning(f"[{i}/{n}] make_frames 결과 없음, 건너뜀")
            time.sleep(1)
            continue

        success_all_frames = True
        logging.debug(f"[{i}/{n}] 총 {len(frames)}개 프레임 전송 시작...")
        for j, f in enumerate(frames, 1):
            # f의 구조: [SEQ (1B)] [TOTAL (1B)] [PAYLOAD_CHUNK (최대 56B)]
            # FRAME_MAX는 LEN 바이트를 제외한 f의 최대 길이
            if len(f) > FRAME_MAX:
                logging.error(f"[{i}/{n}] 프레임(f) 크기 초과: {len(f)} > {FRAME_MAX}")
                success_all_frames = False
                break
            
            # pkt 구조: [LEN (1B)] + f
            # LEN은 f의 길이
            pkt = bytes([len(f)]) + f 
            
            logging.debug(f"[{i}/{n}] 프레임 {j}/{len(frames)} 전송 (LEN={len(f)}, pkt_total_len={len(pkt)}): {pkt!r}")
            if not _tx(s, pkt):
                logging.error(f"[{i}/{n}] 프레임 {j}/{len(frames)} 전송 실패")
                success_all_frames = False
                break
            time.sleep(DELAY_BETWEEN) # 각 프레임 전송 후 딜레이

        if success_all_frames:
            ok += 1
            logging.info(f"[{i}/{n}] 전송 성공 (프레임 {len(frames)}개)")
        else:
            logging.error(f"[{i}/{n}] 전송 실패")

        time.sleep(1) # 다음 센서 데이터 샘플링 및 전송 사이의 딜레이

    logging.info(f"총 {n}회 중 {ok}회 성공적으로 메시지 전송 완료")
    s.close()
    return ok


if __name__ == "__main__":
    # 더 자세한 로그를 보려면 아래 주석 해제
    # logging.getLogger().setLevel(logging.DEBUG)
    send_data()