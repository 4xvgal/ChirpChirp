import time
import logging
import serial
import argparse

from e22_config import init_serial
from packetizer import split_into_packets
from encoder import compress_data

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 패킷당 최대 LoRa 전송 바이트 수(헤더 포함)
HEADER_SIZE = 2  # seq(1 byte) + total(1 byte)
MAX_PAYLOAD_SIZE = 50 - HEADER_SIZE


def send_packet(data: bytes) -> bool:
    """
    단일 LoRa 패킷을 전송합니다.

    Args:
        data: 전송할 바이트 데이터
    Returns:
        bool: 성공 시 True, 실패 시 False
    """
    ser = None
    try:
        ser = init_serial()
        logging.info(f"시리얼 포트 열림: {ser.port} @ {ser.baudrate}bps")
        time.sleep(0.1)

        written = ser.write(data)
        ser.flush()

        if written == len(data):
            logging.info(f"패킷 전송 성공 ({written}/{len(data)} bytes)")
            return True
        else:
            logging.warning(f"부분 전송 발생 ({written}/{len(data)} bytes)")
            return False

    except serial.SerialTimeoutException:
        logging.error("쓰기 타임아웃 발생")
        return False
    except serial.SerialException as e:
        logging.error(f"시리얼 통신 오류: {e}")
        return False
    except Exception as e:
        logging.error(f"예기치 않은 오류: {e}")
        return False
    finally:
        if ser and ser.is_open:
            ser.close()
            logging.info("시리얼 포트 닫힘")


def send_data(obj) -> bool:
    """
    Python 객체를 압축하여 패킷 단위로 분할 송신합니다.

    Args:
        obj: JSON 직렬화 가능한 Python 객체
    Returns:
        bool: 전체 전송 성공 시 True, 중간 실패 시 False
    """
    compressed = compress_data(obj)
    packets = split_into_packets(compressed, max_size=MAX_PAYLOAD_SIZE)
    total = len(packets)
    logging.info(f"총 {total}개의 패킷으로 분할됨")

    for pkt in packets:
        seq = pkt['seq']
        payload = pkt['payload']
        header = bytes([seq, total])
        frame = header + payload

        logging.info(f"패킷 {seq}/{total} 전송 중...")
        if not send_packet(frame):
            logging.error(f"패킷 {seq} 전송 실패. 전체 전송 중단.")
            return False
        time.sleep(0.05)

    logging.info("모든 패킷 전송 완료")
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LoRa 송신 테스트 유틸리티')
    parser.add_argument('--mode', choices=['packet', 'data'], default='data',
                        help='packet: raw 바이트 패킷 테스트, data: Python 객체 전송 테스트')
    args = parser.parse_args()

    if args.mode == 'packet':
        dummy = b'\xAA\xBBTESTPACKET'
        logging.info("[테스트] send_packet 함수 실행")
        result = send_packet(dummy)
        logging.info(f"send_packet 결과: {'성공' if result else '실패'}")
    else:
        sample = {'message': 'Hello LoRa', 'value': 123, 'timestamp': time.time()}
        logging.info("[테스트] send_data 함수 실행")
        result = send_data(sample)
        logging.info(f"send_data 결과: {'성공' if result else '실패'}")

