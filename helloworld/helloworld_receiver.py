import serial


serial_port = '/dev/ttyS0'
baud_rate = 9600

try:
    ser = serial.Serial(serial_port, baud_rate, timeout=1)
    print("수신 대기중...")
    while True:
        data = ser.readline().decode('utf-8', errors='ignore')
        if data:
            print("수신:", data.strip())
except Exception as e:
    print("오류 발생:", e)