# sender.py
import time
import logging
import serial
import json
import base64

from e22_config import init_serial
from packetizer import split_into_packets
from encoder import compress_data

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 패킷당 최대 LoRa 전송 바이트 수 (헤더 포함)
HEADER_SIZE = 2
MAX_PAYLOAD_SIZE = 50 - HEADER_SIZE


def open_serial():
    """
    시리얼 포트를 초기화하여 반환합니다.
    """
    ser = init_serial()
    logging.info(f"시리얼 포트 열림: {ser.port} @ {ser.baudrate}bps")
    time.sleep(0.1)
    return ser


def close_serial(ser):
    """
    열린 시리얼 포트를 닫습니다.
    """
    if ser and ser.is_open:
        ser.close()
        logging.info("시리얼 포트 닫힘")


def send_packet(ser, data: bytes) -> bool:
    """
    단일 LoRa 패킷(바이너리)을 전송합니다.
    """
    try:
        written = ser.write(data)
        ser.flush()
        if written == len(data):
            logging.info(f"패킷 전송 성공 ({written}/{len(data)} bytes)")
            return True
        else:
            logging.warning(f"부분 전송 발생 ({written}/{len(data)} bytes)")
            return False
    except serial.SerialException as e:
        logging.error(f"시리얼 오류: {e}")
        return False


def _send_once(obj) -> bool:
    """
    Python 객체를 한 번 전송합니다.
    객체를 JSON 직렬화→zlib 압축→패킷 분할→Base64 인코딩→JSON 전송
    """
    compressed = compress_data(obj)
    packets = split_into_packets(compressed, max_size=MAX_PAYLOAD_SIZE)
    total = len(packets)
    logging.info(f"[단일 전송] 총 {total}개의 패킷 분할됨")

    ser = open_serial()
    try:
        for pkt in packets:
            seq = pkt['seq']
            payload = pkt['payload']
            b64 = base64.b64encode(payload).decode('ascii')
            packet_json = json.dumps({'seq': seq, 'total': total, 'payload': b64}) + '\n'
            if not send_packet(ser, packet_json.encode('utf-8')):
                logging.error(f"패킷 {seq}/{total} 전송 실패, 중단")
                return False
            time.sleep(0.05)
        logging.info("[단일 전송] 완료")
        return True
    finally:
        close_serial(ser)


def send_data(obj, count: int = 100) -> int:
    """
    주어진 객체를 지정한 횟수만큼 연속 전송합니다.

    Args:
        obj: JSON 직렬화 가능한 Python 객체
        count: 전송 반복 횟수 (기본값 100)
    Returns:
        성공적으로 전송된 횟수
    """
    success = 0
    for i in range(1, count + 1):
        logging.info(f"[반복 전송] {i}/{count}번째 전송 시작")
        if _send_once(obj):
            success += 1
        else:
            logging.error(f"[반복 전송] {i}/{count}번째 전송 실패, 중단")
            break
    logging.info(f"[반복 전송] 완료: {success}/{count} 성공")
    return success


# 테스트 코드
if __name__ == '__main__':
    # 샘플 데이터 생성
    sample_obj = {'message': 'Hello Test', 'value': 42, 'timestamp': time.time()}

    # 단일 전송 테스트
    logging.info("[테스트] _send_once() 단일 전송 테스트 시작")
    result_once = _send_once(sample_obj)
    logging.info(f"[테스트] _send_once 결과: {'성공' if result_once else '실패'}")

    # 반복 전송 테스트
    logging.info("[테스트] send_data() 반복 전송 테스트 시작 (기본 100회)")
    result_count = send_data(sample_obj)