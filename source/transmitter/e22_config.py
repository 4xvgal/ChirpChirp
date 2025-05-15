# e22_config.py
import serial

# 변경이 필요한 설정값
SERIAL_PORT    = '/dev/ttyAMA0'
BAUD_RATE      = 9600
WRITE_TIMEOUT  = 2    # 초
READ_TIMEOUT   = 1    # 초

def init_serial() -> serial.Serial:

    return serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT
    )
