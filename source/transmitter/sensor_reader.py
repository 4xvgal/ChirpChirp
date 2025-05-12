# -*- coding: utf-8 -*-
"""sensor_reader.py – 실 MPU6050 + 모형 GPS
· UART 115200 @ /dev/ttyAMA2 (env MPU_PORT 로 변경 가능)
· 핸드오프 실패 시 Mock MPU로 자동 대체
· get_sensor_data() → 실시간 딕트 반환
· run_logger() – data/raw/ 에 JSONL + CSV 1 000 샘플 저장
"""

from __future__ import annotations

import os, time, json, csv, struct, serial, logging, random
from collections import deque

# ────────── 설정 ──────────
MPU_PORT = os.getenv("MPU_PORT", "/dev/ttyAMA2")  # 기본 포트
MPU_BAUD = 115200
LOG_DIR  = "data/raw"; os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ────────── MPU 파서 ──────────
class _RealMPU:
    def __init__(self, port: str):
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
        self.buf.extend(self.ser.read(33))
        updated = {}
        while len(self.buf) >= 11:
            if self.buf[0] != 0x55:
                self.buf.popleft(); continue
            pkt = bytes([self.buf.popleft() for _ in range(11)])
            r = self._parse(pkt)
            if r: updated.update(r)
        if updated: self.last.update(updated)
        return self.last or None

# ────────── Mock MPU / GPS ──────────
class _MockMPU:
    def poll(self):
        return {
            "accel": {k: round(random.uniform(-2, 2), 2) for k in ("ax", "ay", "az")},
            "gyro":  {k: round(random.uniform(-250, 250), 1) for k in ("gx", "gy", "gz")},
            "angle": {k: round(random.uniform(-180, 180), 1) for k in ("roll", "pitch", "yaw")},
        }

class _MockGPS:
    def poll(self):
        return {"lat": round(random.uniform(33, 38), 6), "lon": round(random.uniform(126, 130), 6)}

# ────────── SensorReader ──────────
class SensorReader:
    def __init__(self):
        try:
            self.m = _RealMPU(MPU_PORT)
        except (serial.SerialException, FileNotFoundError, OSError) as e:
            logging.warning(f"MPU 포트 열기 실패({e}) → Mock MPU 사용")
            self.m = _MockMPU()
        self.g = _MockGPS()

    def get_sensor_data(self):
        d = {"ts": time.time()}
        d.update(self.m.poll() or {})
        d["gps"] = self.g.poll()
        return d

# ────────── 로거 유틸 ──────────

def run_logger(rate: float = 10.0, target: int = 1000):
    sr = SensorReader(); interval = 1.0 / rate
    ts = int(time.time())
    json_path = os.path.join(LOG_DIR, f"log_{ts}.jsonl")
    csv_path  = os.path.join(LOG_DIR, f"log_{ts}.csv")

    fields = ["ts", "ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw", "lat", "lon"]
    with open(json_path, "w") as fj, open(csv_path, "w", newline="") as fc:
        cw = csv.DictWriter(fc, fieldnames=fields); cw.writeheader()
        for i in range(target):
            t0 = time.time(); s = sr.get_sensor_data()
            fj.write(json.dumps(s) + "\n")
            row = {k: s.get(k) or s.get('accel', {}).get(k) or s.get('gyro', {}).get(k) or s.get('angle', {}).get(k) or s.get('gps', {}).get(k, '') for k in fields}
            cw.writerow(row)
            time.sleep(max(0, interval - (time.time() - t0)))
    logging.info(f"logged {target} samples → {json_path}, {csv_path}")

if __name__ == "__main__":
    run_logger()
