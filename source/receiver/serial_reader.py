# serial_reader.py
import time
import serial
from config import SERIAL_PORT, BAUD_RATE, TIMEOUT

class SerialReader:
    """
    하드웨어 시리얼 포트로부터 라인 단위 메시지를 읽어오는 클래스
    """
    def __init__(self, port: str = SERIAL_PORT, baud: int = BAUD_RATE, timeout: float = TIMEOUT):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(1.0)  # 포트 안정화

    def read_line(self) -> str:
        raw = self.ser.readline()
        return raw.decode('utf-8', errors='ignore').strip()

    def close(self):
        self.ser.close()