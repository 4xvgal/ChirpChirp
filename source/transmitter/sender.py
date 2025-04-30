# sender.py
import time
import logging
import serial
import json
import base64

from e22_config import init_serial
from packetizer import split_into_packets
from encoder import compress_data
from sensor_reader import SensorReader  # 센서 리더 모듈 임포트

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
        logging.warning(f"부분 전송 발생 ({written}/{len(data)} bytes)")
        return False
    except serial.SerialException as e:
        logging.error(f"시리얼 오류: {e}")
        return False


def _send_once(data_obj) -> bool:
    """
    주어진 센서 데이터 객체를 한 번 전송합니다.
    JSON 직렬화→zlib 압축→패킷 분할→Base64 인코딩→JSON 전송
    """
    compressed = compress_data(data_obj)
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


def send_data(count: int = 100) -> int:
    """
    SensorReader를 이용해 센서 데이터를 count회 연속 전송합니다.

    Args:
        count: 전송 반복 횟수 (기본값 100)
    Returns:
        성공적으로 전송된 횟수
    """
    reader = SensorReader()
    success = 0
    for i in range(1, count + 1):
        data = reader.get_sensor_data()
        logging.info(f"[반복 전송] {i}/{count}번째 전송 시작 - 데이터: {data}")
        if _send_once(data):
            success += 1
        else:
            logging.error(f"[반복 전송] {i}/{count}번째 전송 실패, 중단")
            break
        # 전송 간 1초 대기
        time.sleep(1)
    logging.info(f"[반복 전송] 완료: {success}/{count} 성공")
    return success


# 테스트 코드
if __name__ == '__main__':
    # 반복 전송 테스트
    logging.info("[테스트] send_data() 반복 전송 테스트 시작 (기본 100회)")
    result_count = send_data()
    logging.info(f"[테스트] send_data 결과: {result_count}/100 성공")
