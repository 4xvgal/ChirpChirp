# main.py
#!/usr/bin/env python3
import os
import sys
import signal
import time
import datetime

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from config import LOG_DIR
from serial_reader import SerialReader
from session_logger import SessionLogger
from packet_reassembler import PacketReassembler, PacketFormatError, PacketReassemblyError
from decoder import decompress_data
from plotter import Plotter

# Ctrl-C 안전 종료 핸들러
def signal_handler(sig, frame):
    print("\n[Receiver] 중단 요청, 종료합니다.")
    sys.exit(0)

# 로거 초기화
def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    fn = f"session_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return SessionLogger(os.path.join(LOG_DIR, fn))


def main():
    signal.signal(signal.SIGINT, signal_handler)
    logger = setup_logger()
    reader = SerialReader()
    reassembler = PacketReassembler()
    plotter = Plotter()

    start_time = time.time()
    unique_ids = set()

    print(f"[Receiver] 시작 → port={reader.ser.port}, baud={reader.ser.baudrate}")

    try:
        while True:
            raw = reader.read_line()
            if not raw:
                continue

            parts = raw.split('|')

            # PDR 계산 및 업데이트
            if len(parts) >= 2:
                try:
                    pkt_id = int(parts[1])
                except ValueError:
                    pkt_id = None
                if pkt_id is not None:
                    unique_ids.add(pkt_id)
                    pdr = len(unique_ids) / (max(unique_ids) + 1) * 100
                    elapsed = time.time() - start_time
                    plotter.update(elapsed, pdr)
                    print(f"[Receiver] {raw} | PDR: {pdr:.2f}%")

            # 패킷 재조립 및 로그
            if len(parts) == 3 and '/' in parts[1]:
                try:
                    data = reassembler.process_line(raw)
                    if data is not None:
                        sensor = decompress_data(data)
                        if sensor is not None:
                            entry = {'timestamp': datetime.datetime.utcnow().isoformat(), **sensor}
                            logger.log(entry)
                            print(f"[Receiver] 데이터 로깅: {entry}")
                except (PacketFormatError, PacketReassemblyError):
                    pass
                except Exception as e:
                    print(f"[Receiver] 처리 오류: {e}")

    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        logger.close()
        plotter.close()
        print("[Receiver] 종료 및 로그 저장 완료.")

if __name__ == '__main__':
    main()
