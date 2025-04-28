# sender.py
import serial, time, json, base64
from sensor_reader import SensorReader

SERIAL_PORT = '/dev/ttyS0'
BAUD_RATE    = 9600
MAX_CHUNK    = 50    # 바이트 단위

def split_bytes(data: bytes, size: int):
    return [ data[i:i+size] for i in range(0, len(data), size) ]

def send_sensor_packets():
    # 1) 시리얼 포트 오픈
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(1)

    # 2) 센서 데이터 취득 및 JSON 직렬화
    reader = SensorReader()
    sensor_dict = reader.get_sensor_data()
    raw = json.dumps(sensor_dict).encode('utf-8')

    # 3) 바이트 분할 → Base64 인코딩 → JSON 패킷 생성
    chunks = split_bytes(raw, MAX_CHUNK)
    total = len(chunks)

    for seq, chunk in enumerate(chunks, start=1):
        b64 = base64.b64encode(chunk).decode('ascii')
        packet = {"seq": seq, "total": total, "payload": b64}
        line = json.dumps(packet) + "\n"

        ser.write(line.encode('utf-8'))
        print(f"[Sender] 보낸 패킷 {seq}/{total}")
        time.sleep(0.05)  # 흐름 조절

    ser.close()

if __name__ == "__main__":
    send_sensor_packets()