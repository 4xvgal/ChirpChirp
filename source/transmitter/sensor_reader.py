# sensor_reader.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import os, time, json, csv, struct, serial, logging, random
from collections import deque

MPU_PORT = os.getenv("MPU_PORT", "/dev/ttyAMA2")  # 기본 포트
MPU_BAUD = 115200
LOG_DIR  = "data/raw"; os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class _RealMPU:
    def __init__(self, port: str):
        # MPU 연결 실패 시 여기서 예외가 발생하고, SensorReader에서 처리됨
        self.ser = serial.Serial(port, MPU_BAUD, timeout=0.05)
        self.buf: deque[int] = deque()
        self.last: dict = {}
        logging.info(f"MPU 연결 성공: {port}")

    def _s16(self, b):
        return struct.unpack('<h', b)[0]

    def _parse(self, p: bytes):
        if p[0] != 0x55: return None
        kind = p[1]
        if kind == 0x51:
            return {"accel": {
                "ax": self._s16(p[2:4]) / 32768 * 16,
                "ay": self._s16(p[4:6]) / 32768 * 16,
                "az": self._s16(p[6:8]) / 32768 * 16}}
        if kind == 0x52:
            return {"gyro": {
                "gx": self._s16(p[2:4]) / 32768 * 2000,
                "gy": self._s16(p[4:6]) / 32768 * 2000,
                "gz": self._s16(p[6:8]) / 32768 * 2000}}
        if kind == 0x53:
            return {"angle": {
                "roll":  self._s16(p[2:4]) / 32768 * 180,
                "pitch": self._s16(p[4:6]) / 32768 * 180,
                "yaw":   self._s16(p[6:8]) / 32768 * 180}}
        return None

    def poll(self):
        try:
            if not self.ser.is_open: # 혹시 모를 시리얼 포트 닫힘 상황 대비
                logging.error("MPU 시리얼 포트가 닫혀있습니다. 재연결 시도 안 함.")
                # 이 경우, get_sensor_data에서 빈 MPU 데이터를 반환하거나 예외를 발생시킬 수 있음
                # 여기서는 poll() 호출 시 비어있는 self.last를 반환하게 될 가능성
                return self.last or None # 또는 예외 발생
            
            self.buf.extend(self.ser.read(33))
        except serial.SerialException as e:
            logging.error(f"MPU 데이터 읽기 중 시리얼 오류 발생: {e}")
            return self.last or None # 이전 값 또는 빈 값 반환

        updated = {}
        while len(self.buf) >= 11:
            if self.buf[0] != 0x55:
                self.buf.popleft(); continue
            pkt_bytes = []
            for _ in range(11): 
                if not self.buf : # 버퍼가 예상보다 빨리 비면 중단
                    logging.warning("MPU 데이터 파싱 중 예상치 못하게 버퍼가 비었습니다.")
                    break
                pkt_bytes.append(self.buf.popleft())
            
            if len(pkt_bytes) == 11:
                pkt = bytes(pkt_bytes)
                r = self._parse(pkt)
                if r: updated.update(r)
            else: 
                # 소비하지 못한 바이트를 다시 버퍼 앞에 넣어줄 수도 있지만, 복잡해지므로 일단 로그만 남김
                logging.warning(f"MPU 패킷 구성 중 바이트 부족 (필요: 11, 실제: {len(pkt_bytes)}). 일부 데이터 유실 가능성.")


        if updated: self.last.update(updated)
        return self.last or None

class _MockGPS:
    def poll(self):
        return {"lat": round(random.uniform(33, 38), 6), "lon": round(random.uniform(126, 130), 6)}

# ────────── SensorReader ──────────
class SensorReader:
    def __init__(self):
        try:
            self.m = _RealMPU(MPU_PORT)
        except (serial.SerialException, FileNotFoundError, OSError) as e:
            # MPU 연결 실패 시, 경고 로깅 후 예외를 다시 발생시켜 프로그램 중단 유도
            logging.error(f"필수 MPU 센서({MPU_PORT}) 연결 실패: {e}. 프로그램을 계속할 수 없습니다.")
            raise  # 예외를 다시 발생시켜 호출자(예: main 프로그램)가 처리하도록 함
        
        self.g = _MockGPS() # GPS는 계속 Mock 사용

    def get_sensor_data(self):
        d = {"ts": time.time()}
        
        mpu_data = None
        try:
            if hasattr(self, 'm') and self.m is not None:
                 mpu_data = self.m.poll()
            else: # MPU 초기화 실패 시 self.m이 없을 수 있음 (위의 __init__에서 raise 하므로 이 경우는 드묾)
                logging.error("MPU 객체가 초기화되지 않았습니다.")
                mpu_data = {}

        except Exception as e: # MPU poll 중 발생할 수 있는 예외 처리
            logging.error(f"MPU 데이터 가져오는 중 오류 발생: {e}")
            mpu_data = {} # 오류 시 빈 MPU 데이터 또는 이전 데이터 사용 고려
                          # 여기서는 빈 데이터를 사용하도록 함

        d.update(mpu_data or {}) # mpu_data가 None일 경우 빈 dict로 처리
        d["gps"] = self.g.poll()
        return d

def run_logger(rate: float = 10.0, target: int = 1000):
    try:
        sr = SensorReader() # SensorReader 초기화 시 MPU 연결 실패하면 여기서 예외 발생 및 종료
    except Exception as e: # SensorReader 초기화 실패(MPU 연결 실패 등) 시
        logging.critical(f"SensorReader 초기화 실패로 로거 실행 불가: {e}")
        return # 프로그램 종료 또는 다른 오류 처리

    interval = 1.0 / rate
    ts_start = int(time.time()) # 파일명에 사용할 타임스탬프
    json_path = os.path.join(LOG_DIR, f"log_{ts_start}.jsonl")
    csv_path  = os.path.join(LOG_DIR, f"log_{ts_start}.csv")

    fields = ["ts", "ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw", "lat", "lon"]
    
    # 파일 열기 시에도 예외 처리 추가
    try:
        with open(json_path, "w", encoding="utf-8") as fj, \
             open(csv_path, "w", newline="", encoding="utf-8") as fc:
            
            cw = csv.DictWriter(fc, fieldnames=fields)
            cw.writeheader()
            
            logging.info(f"{target}개의 샘플 로깅 시작...")
            for i in range(target):
                t0 = time.time()
                s = sr.get_sensor_data() # MPU 데이터 읽기 실패 시 빈 데이터가 올 수 있음
                
                # JSON 저장
                fj.write(json.dumps(s) + "\n")
                
                # CSV 저장용 데이터 준비 (get의 기본값을 사용하여 키 부재 방지)
                row_data = {
                    "ts": s.get("ts", ""),
                    "ax": s.get("accel", {}).get("ax", ""),
                    "ay": s.get("accel", {}).get("ay", ""),
                    "az": s.get("accel", {}).get("az", ""),
                    "gx": s.get("gyro", {}).get("gx", ""),
                    "gy": s.get("gyro", {}).get("gy", ""),
                    "gz": s.get("gyro", {}).get("gz", ""),
                    "roll": s.get("angle", {}).get("roll", ""),
                    "pitch": s.get("angle", {}).get("pitch", ""),
                    "yaw": s.get("angle", {}).get("yaw", ""),
                    "lat": s.get("gps", {}).get("lat", ""),
                    "lon": s.get("gps", {}).get("lon", "")
                }
                cw.writerow(row_data)
                
                # 루프 주기 맞추기
                elapsed_time = time.time() - t0
                sleep_duration = max(0, interval - elapsed_time)
                time.sleep(sleep_duration)
                
                if (i + 1) % (rate * 10) == 0: # 약 10초마다 진행 상황 로깅
                    logging.info(f"진행: {i+1}/{target} 샘플 로깅됨.")

    except IOError as e:
        logging.error(f"로그 파일 작업 중 오류 발생 ({json_path} 또는 {csv_path}): {e}")
    except Exception as e:
        logging.error(f"로깅 중 예기치 않은 오류 발생: {e}")
    
    logging.info(f"로깅 완료: {target} 샘플. 파일: {json_path}, {csv_path}")


if __name__ == "__main__":
    try:
        run_logger()
    except Exception as e:
        logging.critical(f"run_logger 실행 중 치명적 오류 발생: {e}")
        # 여기서 프로그램 종료 또는 추가적인 정리 작업 수행 가능