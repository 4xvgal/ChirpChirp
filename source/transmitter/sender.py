# sender.py
import time
import logging
import serial
import argparse
import json
import base64

from e22_config import init_serial
from packetizer import split_into_packets  # 바이너리 분할 로직
from encoder import compress_data         # zlib 압축 로직

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 패킷당 최대 LoRa 전송 바이트 수(헤더 포함)
HEADER_SIZE = 2  # seq(1 byte) + total(1 byte)
MAX_PAYLOAD_SIZE = 50 - HEADER_SIZE  # raw 바이너리 기준


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


def send_data(obj) -> bool:
    """
    Python 객체를 JSON 직렬화 → zlib 압축 → 패킷 분할 → Base64 인코딩 → JSON 전송

    Args:
        obj: JSON 직렬화 가능한 Python 객체
    Returns:
        bool: 전체 전송 성공 시 True
    """
    # 1) JSON 직렬화 + zlib 압축
    compressed = compress_data(obj)

    # 2) raw 바이너리 분할
    packets = split_into_packets(compressed, max_size=MAX_PAYLOAD_SIZE)
    total = len(packets)
    logging.info(f"총 {total}개의 패킷으로 분할됨")

    # 3) 시리얼 오픈
    ser = open_serial()
    try:
        for pkt in packets:
            seq = pkt['seq']
            payload = pkt['payload']  # raw 바이너리

            # 4) Base64 인코딩 + JSON 포맷
            b64 = base64.b64encode(payload).decode('ascii')
            packet_json = json.dumps({
                'seq': seq,
                'total': total,
                'payload': b64
            }) + '\n'

            # 5) JSON 텍스트 전송
            logging.info(f"패킷 {seq}/{total} 전송 중...")
            if not send_packet(ser, packet_json.encode('utf-8')):
                logging.error(f"패킷 {seq} 전송 실패. 중단합니다.")
                return False

            time.sleep(0.05)

        logging.info("모든 패킷 전송 완료")
        return True

    finally:
        close_serial(ser)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LoRa 송신 테스트 유틸리티')
    parser.add_argument('--mode', choices=['packet', 'data'], default='data',
                        help='packet: raw 바이너리 테스트, data: 센서 데이터 전송')
    args = parser.parse_args()

    if args.mode == 'packet':
        # 단일 raw 패킷 전송 테스트
        ser = open_serial()
        result = send_packet(ser, b'\xAA\xBBTESTPACKET')
        logging.info(f"send_packet 결과: {'성공' if result else '실패'}")
        close_serial(ser)
    else:
        # 센서 데이터 전송 테스트
        import sensor_reader
        reader = sensor_reader.SensorReader()
        data = reader.get_sensor_data()
        logging.info("[테스트] send_data 함수 실행")
        result = send_data(data)
        logging.info(f"send_data 결과: {'성공' if result else '실패'}")
