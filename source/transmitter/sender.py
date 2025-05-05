# -*- coding: utf-8 -*-
from __future__ import annotations

import time, logging, serial, json, base64, datetime # datetime 추가
from typing import Any, Dict

# 가정: e22_config, packetizer, encoder, sensor_reader 모듈은 동일 경로에 존재
from e22_config import init_serial
from packetizer import split_into_packets
from encoder import compress_data
from sensor_reader import SensorReader

HEADER_SIZE = 2
LORA_FRAME_LIMIT = 58
JSON_OVERHEAD   = 29
RAW_MAX_PER_PKT = 5     # 47B JSON (안전)
MAX_RETRY = 3 # 데이터 패킷 전송 재시도 횟수

# 핸드셰이크 설정
HANDSHAKE_TIMEOUT = 2.0 # ACK 응답 대기 시간 (초)
SYN_MSG = b"SYN\r\n"    # 보낼 때는 bytes
ACK_MSG = "ACK"         # 받을 때는 string으로 비교

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# 시리얼 포트 열기 함수 (에러 처리 강화)
def _open_serial() -> serial.Serial | None:
    try:
        s = init_serial()
        s.timeout = HANDSHAKE_TIMEOUT # ACK 수신을 위한 읽기 타임아웃 설정
        time.sleep(0.1) # 안정화 시간
        return s
    except serial.SerialException as e:
        logging.error(f"시리얼 포트 열기 실패: {e}")
        return None
    except Exception as e:
        logging.error(f"시리얼 초기화 중 예외 발생: {e}")
        return None

_close = lambda s: s.is_open and s.close() if s else None

# 데이터 전송 함수 (재시도 포함)
def _tx(s: serial.Serial, buf: bytes) -> bool:
    for attempt in range(MAX_RETRY):
        try:
            written = s.write(buf)
            if written == len(buf):
                s.flush()
                # logging.debug(f"TX ({written}B): {buf.decode('utf-8', errors='ignore').strip()}") # 디버깅용
                return True
            else:
                logging.warning(f"부분 전송됨: {written}/{len(buf)} bytes (Attempt {attempt + 1}/{MAX_RETRY})")
        except serial.SerialException as e:
            logging.error(f"전송 실패: {e} (Attempt {attempt + 1}/{MAX_RETRY})")
        except Exception as e:
             logging.error(f"전송 중 예외: {e} (Attempt {attempt + 1}/{MAX_RETRY})")
        time.sleep(0.3) # 재시도 전 잠시 대기
    logging.error(f"최종 전송 실패: {buf.decode('utf-8', errors='ignore').strip()}")
    return False

# 핸드셰이크 수행 함수
def _do_handshake(s: serial.Serial) -> bool:
    """SYN을 보내고 ACK를 기다립니다."""
    logging.info("핸드셰이크 시작: SYN 전송 시도...")
    if not _tx(s, SYN_MSG):
        logging.error("핸드셰이크 실패: SYN 전송 실패")
        return False

    logging.info(f"SYN 전송 완료. {HANDSHAKE_TIMEOUT}초 동안 ACK 대기...")
    try:
        # ACK 응답 읽기 시도
        ack_line_bytes = s.readline()
        # logging.debug(f"Raw ACK received: {ack_line_bytes}") # 디버깅용

        if ack_line_bytes:
            ack_line = ack_line_bytes.decode('utf-8', errors='ignore').strip()
            if ack_line == ACK_MSG:
                logging.info("ACK 수신 성공. 핸드셰이크 완료.")
                return True
            else:
                logging.warning(f"핸드셰이크 실패: 예상치 못한 응답 수신 '{ack_line}'")
                return False
        else:
            # readline()이 비어있는 바이트를 반환하면 타임아웃 발생
            logging.warning(f"핸드셰이크 실패: ACK 수신 타임아웃 ({HANDSHAKE_TIMEOUT}초)")
            return False
    except serial.SerialException as e:
        logging.error(f"ACK 수신 중 시리얼 오류: {e}")
        return False
    except UnicodeDecodeError as ude:
         logging.error(f"ACK 응답 디코딩 오류: {ude} (Raw: {ack_line_bytes})")
         return False
    except Exception as e:
        logging.error(f"ACK 수신 중 예외 발생: {e}")
        return False


def _send_once(data: Dict[str, Any]) -> bool:
    """핸드셰이크 후 데이터 패킷들을 전송합니다."""
    s = _open_serial()
    if not s:
        return False # 포트 열기 실패

    try:
        # 1. 핸드셰이크 수행
        if not _do_handshake(s):
            return False # 핸드셰이크 실패 시 데이터 전송 안 함

        # 2. 핸드셰이크 성공 시 데이터 준비 및 전송
        compressed_data = compress_data(data)
        if not compressed_data:
            logging.error("데이터 압축 실패.")
            return False

        pkts = split_into_packets(compressed_data, RAW_MAX_PER_PKT)
        tot = len(pkts)
        logging.info(f"데이터 전송 시작: 총 {tot} 패킷")

        # 중요: 데이터 전송 전 시리얼 타임아웃을 짧게 변경 (선택 사항)
        # 핸드셰이크 후에는 읽을 일이 없으므로 타임아웃을 줄여 불필요한 대기 방지
        s.timeout = 0.1 # 예: 100ms

        for i, p in enumerate(pkts):
            # 패킷 생성 (JSON + Base64)
            packet_data = {
                "seq": p["seq"],
                "total": tot,
                "payload": base64.b64encode(p["payload"]).decode()
            }
            line = json.dumps(packet_data) + "\r\n"
            line_bytes = line.encode('utf-8')

            # LoRa 프레임 크기 확인
            if len(line_bytes) > LORA_FRAME_LIMIT:
                 logging.error(f"패킷 크기 초과 ({len(line_bytes)} > {LORA_FRAME_LIMIT}): {line.strip()}")
                 return False # 너무 큰 패킷은 전송 중단

            # 패킷 전송 시도
            logging.info(f"패킷 전송 ({i+1}/{tot}) - {len(line_bytes)} bytes")
            if not _tx(s, line_bytes):
                logging.error(f"패킷 {i+1}/{tot} 전송 실패.")
                return False # 하나라도 전송 실패 시 중단

            # LoRa 전송 간 딜레이 (필수)
            time.sleep(0.3) # 패킷 간 간격 조절

        logging.info(f"모든 패킷 ({tot}) 전송 완료.")
        return True

    except Exception as e:
        logging.error(f"_send_once 실행 중 예외 발생: {e}")
        return False
    finally:
        # 항상 시리얼 포트 닫기
        if s and s.is_open:
            logging.debug("시리얼 포트 닫는 중...")
            s.close()


def send_data(n: int = 100) -> int:
    """지정된 횟수(n)만큼 센서 데이터를 읽고 전송 시도"""
    r = SensorReader()
    ok = 0
    for i in range(1, n + 1):
        logging.info(f"===== 전송 시도 {i}/{n} 시작 =====")
        sensor_reading = r.get_sensor_data()
        if not sensor_reading:
            logging.warning(f"센서 데이터 읽기 실패 (시도 {i}/{n}).")
            time.sleep(1) # 다음 시도 전 대기
            continue

        logging.info(f"전송할 데이터: {sensor_reading}")
        if _send_once(sensor_reading):
            logging.info(f"시도 {i}/{n} 성공.")
            ok += 1
        else:
            logging.error(f"시도 {i}/{n} 실패.")
            # 실패 시 바로 중단할지, 계속 시도할지 결정 가능
            # break # 실패 시 중단하려면 주석 해제

        # 각 전송 시도 후 잠시 대기 (LoRa 네트워크 부하 감소)
        time.sleep(1)
        logging.info(f"===== 전송 시도 {i}/{n} 종료 =====")

    logging.info(f"총 {n}번 시도 중 {ok}번 성공.")
    return ok

if __name__ == "__main__":
    send_data(5) # 테스트를 위해 횟수를 줄임