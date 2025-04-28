## config.py
import os
# 설정 상수 모듈
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
LOG_DIR = os.path.join(BASE_DIR, 'logs')

SERIAL_PORT = '/dev/ttyS0'
BAUD_RATE    = 9600
TIMEOUT      = 1.0
MAX_POINTS   = 500  # 그래프에 표시할 최대 포인트 수

