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
FRAME_MAX         = 2 + MAX_PAYLOAD   # SEQ(1B)+TOTAL(1B)+PAYLOAD_CHUNK
HANDSHAKE_TIMEOUT = 5.0               # 핸드셰이크 타임아웃
SEND_COUNT        = 1000              # 전체 메시지 전송 횟수
RETRY_HANDSHAKE   = 5                 # 핸드셰이크 재시도 횟수
RETRY_FRAME       = 3                 # 프레임 단위 재전송 횟수
DELAY_BETWEEN     = 0.3               # 프레임 간 딜레이 (초)

# 프로토콜 메시지
SYN       = b"SYN\n"
ACK0_FMT  = b"ACK%d\n"               # 프레임별 ACK 포맷

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

def _open_serial() -> serial.Serial:
    try:
        s = init_serial()
        s.timeout = HANDSHAKE_TIMEOUT
        time.sleep(0.1)
        return s
    except serial.SerialException as e:
        logging.error(f"시리얼 포트 열기 실패: {e}")
        raise

def _tx(s: serial.Serial, buf: bytes) -> bool:
    try:
        written = s.write(buf)
        s.flush()
        logging.debug(f"TX: {buf!r}")
        return written == len(buf)
    except Exception as e:
        logging.error(f"TX 실패: {e}")
        return False

def _handshake(s: serial.Serial) -> bool:
    for i in range(1, RETRY_HANDSHAKE+1):
        logging.info(f"핸드셰이크 시도 {i}/{RETRY_HANDSHAKE} - SYN 전송")
        if not _tx(s, SYN):
            logging.warning("SYN 전송 실패, 재시도")
            time.sleep(1)
            continue

        line = s.readline()
        if line == ACK0_FMT % 0:  # 초기 ACK은 SEQ=0 으로 간주
            logging.info("핸드셰이크 성공")
            return True
        else:
            logging.warning(f"잘못된 핸드셰이크 응답: {line!r}")
    logging.error("핸드셰이크 최종 실패")
    return False

def send_data(n: int = SEND_COUNT) -> int:
    try:
        s = _open_serial()
    except Exception:
        return 0

    if not _handshake(s):
        s.close()
        return 0

    # 데이터 전송용 짧은 타임아웃
    s.timeout = 0.1
    sr = SensorReader()
    ok_count = 0

    logging.info(f"--- 총 {n}회 데이터 전송 시작 ---")
    for i in range(1, n+1):
        sample = sr.get_sensor_data()
        if not sample or not all(k in sample for k in ("ts","accel","gyro","angle","gps")):
            logging.warning(f"[{i}/{n}] 불완전 샘플, 건너뜀")
            time.sleep(1)
            continue

        frames = make_frames(sample)
        if not frames:
            logging.warning(f"[{i}/{n}] 프레임 생성 실패, 건너뜀")
            time.sleep(1)
            continue

        success_all = True
        logging.debug(f"[{i}/{n}] {len(frames)}개 프레임 전송")

        for f in frames:
            seq = f[0]
            pkt = bytes([len(f)]) + f
            frame_ok = False

            for attempt in range(1, RETRY_FRAME+1):
                if not _tx(s, pkt):
                    logging.warning(f"[{i}] 프레임{seq} 전송 실패, 재시도 {attempt}/{RETRY_FRAME}")
                    continue

                resp = s.readline()
                expected = ACK0_FMT % seq
                if resp == expected:
                    logging.debug(f"[{i}] 프레임{seq} ACK 수신: {resp!r}")
                    frame_ok = True
                    break
                else:
                    logging.warning(f"[{i}] 프레임{seq} 잘못된 ACK: {resp!r}, 기대: {expected!r}")

            if not frame_ok:
                logging.error(f"[{i}] 프레임{seq} 전송/ACK 실패")
                success_all = False
                break

            time.sleep(DELAY_BETWEEN)

        if success_all:
            ok_count += 1
            logging.info(f"[{i}/{n}] 메시지 전송 성공")
        else:
            logging.info(f"[{i}/{n}] 메시지 전송 실패")

        time.sleep(1)

    logging.info(f"전송 완료: {n}회 중 {ok_count}회 성공")
    s.close()
    return ok_count

if __name__ == "__main__":
    # 자세한 로그가 필요하면 uncomment:
    # logging.getLogger().setLevel(logging.DEBUG)
    send_data()
