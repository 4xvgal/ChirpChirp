import serial
import time

serial_port = '/dev/ttyS0'
baud_rate = 9600

try:
    ser = serial.Serial(serial_port, baud_rate, timeout=1)
    time.sleep(2)  # 포트 안정화를 위한 대기
    print("helloworld를 반복 전송합니다...")

    while True:
        message = "hello world!\n"
        ser.write(message.encode('utf-8'))
        print("송신:", message.strip())
        time.sleep(1) 
except Exception as e:
    print("오류 발생:", e)
