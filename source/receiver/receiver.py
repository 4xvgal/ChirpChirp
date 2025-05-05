# receiver.py (모듈)
import serial
import time
import datetime

from packet_reassembler import PacketReassembler, PacketFormatError, PacketReassemblyError
import decoder  # 압축 해제 및 데이터 복원 모듈

# 시리얼 설정
SERIAL_PORT = '/dev/serial0'
BAUD_RATE = 9600
DEFAULT_TIMEOUT = 1  # 시리얼 읽기 타임아웃 (초)


def receive_loop(port=SERIAL_PORT, baud=BAUD_RATE, serial_timeout=DEFAULT_TIMEOUT):
    """
    무한 루프를 통해 지속적으로 패킷을 수신하고 처리합니다.
    패킷이 없으면 주기적으로 "대기중.." 메시지를 출력하며, Ctrl-C로 종료합니다.
    """
    reassembler = PacketReassembler()
    ser = serial.Serial(port, baud, timeout=serial_timeout)
    time.sleep(1)  # 포트 안정화

    start_ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{start_ts}] Receiver: 시작됨 ({port}, {baud} baud)")
    last_print = time.time()  # 대기 메시지 마지막 출력 시각
    print(f"[{start_ts}] Receiver: 대기중.. (패킷 수신 대기)")

    try:
        while True:
            if ser.in_waiting > 0:
                # 패킷이 도착했으므로 상세 처리 로직 실행
                line_bytes = ser.readline()
                if not line_bytes:
                    continue

                # UTF-8 디코딩
                try:
                    line = line_bytes.decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                except UnicodeDecodeError as ude:
                    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    print(f"[{ts}] Receiver: 디코딩 실패 - {ude}")
                    continue

                ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                try:
                    reassembled = reassembler.process_line(line)
                    if reassembled is not None:
                        print(f"[{ts}] Receiver: 재조립 완료 - {len(reassembled)} bytes")
                        sensor_data = decoder.decompress_data(reassembled)
                        if sensor_data is not None:
                            print(f"[{ts}] Receiver: 데이터 복원 성공 → {sensor_data}")
                        else:
                            print(f"[{ts}] Receiver: 데이터 복원 실패 (Decoder)")
                        # 처리 완료 후, 다시 대기 상태 알림 준비
                        last_print = time.time()
                except PacketFormatError as pfe:
                    print(f"[{ts}] Receiver: 잘못된 패킷 형식 - {pfe}")
                except PacketReassemblyError as pre:
                    print(f"[{ts}] Receiver: 재조립 오류 - {pre}")
                except Exception as e:
                    print(f"[{ts}] Receiver: 처리 중 예외 - {e}")
            else:
                # 패킷 대기 상태: 주기적으로 메시지 출력
                now = time.time()
                if now - last_print >= 1.0:
                    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"[{ts}] Receiver: 대기중.. (패킷 수신 대기)")
                    last_print = now
                time.sleep(0.01)
    except KeyboardInterrupt:
        print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 사용자에 의해 중단됨.")
    finally:
        if ser and ser.is_open:
            ser.close()
            print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 포트 닫힘")


if __name__ == '__main__':
    receive_loop()
