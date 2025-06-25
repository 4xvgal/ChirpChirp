# sensor_reader.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import os, time, json, csv, struct, serial, logging, random
from collections import deque
from typing import Dict, Any, Optional # 타입 힌팅 추가

MPU_PORT = os.getenv("MPU_PORT", "/dev/ttyAMA2")
MPU_BAUD = 115200
GPS_PORT = os.getenv("GPS_PORT", "/dev/ttyAMA4") # GPS 포트 추가
GPS_BAUD = 9600
LOG_DIR  = "data/raw"; os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# --- MPU 관련 클래스 (기존과 거의 동일) ---
class _RealMPU:
    def __init__(self, port: str):
        self.ser = serial.Serial(port, MPU_BAUD, timeout=0.05)
        self.buf: deque[int] = deque()
        self.last: dict = {}
        logging.info(f"MPU 연결 성공: {port}")

    def _s16(self, b):
        return struct.unpack('<h', b)[0]

    def _parse(self, p: bytes) -> Optional[Dict[str, Any]]:
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

    def poll(self) -> Optional[Dict[str, Any]]:
        try:
            if not self.ser.is_open:
                logging.error("MPU 시리얼 포트가 닫혀있습니다.")
                return self.last or None
            
            self.buf.extend(self.ser.read(33))
        except serial.SerialException as e:
            logging.error(f"MPU 데이터 읽기 중 시리얼 오류 발생: {e}")
            return self.last or None

        updated = {}
        while len(self.buf) >= 11:
            if self.buf[0] != 0x55:
                self.buf.popleft(); continue
            pkt_bytes = []
            for _ in range(11): 
                if not self.buf:
                    logging.warning("MPU 데이터 파싱 중 예상치 못하게 버퍼가 비었습니다.")
                    break
                pkt_bytes.append(self.buf.popleft())
            
            if len(pkt_bytes) == 11:
                pkt = bytes(pkt_bytes)
                r = self._parse(pkt)
                if r: updated.update(r)
            else: 
                logging.warning(f"MPU 패킷 구성 중 바이트 부족 (필요: 11, 실제: {len(pkt_bytes)}).")

        if updated: self.last.update(updated)
        return self.last or None


# --- GPS 관련 클래스 ---
class _MockGPS:
    def __init__(self):
        logging.info("Mock GPS 사용 중입니다.")
        self.data = {"lat": 0.0, "lon": 0.0, "satellites": 0, "fix": "0", "altitude": "0.0"}


    def poll(self) -> Dict[str, Any]:
        # Mock 데이터는 더 현실적인 값으로 업데이트 가능
        self.data["lat"] = round(random.uniform(33, 38), 6)
        self.data["lon"] = round(random.uniform(126, 130), 6)
        self.data["satellites"] = random.randint(0,5) # Mock 위성 수
        self.data["fix"] = "1" if self.data["satellites"] > 3 else "0"
        self.data["altitude"] = str(round(random.uniform(10,100),1)) if self.data["fix"] == "1" else "0.0"
        return self.data

    def is_fixed(self) -> bool:
        return True # Mock GPS는 항상 고정된 것으로 간주

    def get_max_satellites(self) -> int:
        return 5 # Mock 최대 위성 수

class _RealGPS:
    def __init__(self, port: str):
        logging.info(f"실제 GPS 사용 시도: {port}")
        self.ser = serial.Serial(port, GPS_BAUD, timeout=1)
        self.max_satellites_observed = 0
        self.current_data = {"lat": 0.0, "lon": 0.0, "satellites": 0, "fix": "0", "altitude": "0.0"}
        self.last_gga_fields: Optional[list[str]] = None
        self.last_rmc_fields: Optional[list[str]] = None
        logging.info(f"GPS 연결 성공: {port}")


    def _parse_gpgga(self, fields: list[str]) -> None:
        try:
            if len(fields) > 9:
                self.current_data["fix"] = fields[6]
                num_satellites = int(fields[7]) if fields[7] else 0
                self.current_data["satellites"] = num_satellites
                self.current_data["altitude"] = fields[9] if fields[9] else "0.0"

                if num_satellites > self.max_satellites_observed:
                    self.max_satellites_observed = num_satellites
                # logging.debug(f"[GGA] FIX: {self.current_data['fix']} Sat: {num_satellites} Alt: {self.current_data['altitude']}m (Max Obs: {self.max_satellites_observed})")
        except ValueError as e:
            logging.warning(f"GPGGA 파싱 오류 (ValueError): {e} - Fields: {fields}")
        except IndexError as e:
            logging.warning(f"GPGGA 파싱 오류 (IndexError): {e} - Fields: {fields}")


    def _parse_gprmc(self, fields: list[str]) -> None:
        try:
            if len(fields) > 6:
                status = fields[2]
                if status == 'A': # 'A' = Active/OK, 'V' = Void/Warning
                    lat_str = fields[3]
                    lat_dir = fields[4]
                    lon_str = fields[5]
                    lon_dir = fields[6]
                    
                    # NMEA ddmm.mmmm to Decimal Degrees
                    if lat_str and lon_str:
                        lat_deg = int(lat_str[:2])
                        lat_min = float(lat_str[2:])
                        self.current_data["lat"] = round(lat_deg + (lat_min / 60.0), 6)
                        if lat_dir == 'S':
                            self.current_data["lat"] *= -1

                        lon_deg = int(lon_str[:3])
                        lon_min = float(lon_str[3:])
                        self.current_data["lon"] = round(lon_deg + (lon_min / 60.0), 6)
                        if lon_dir == 'W':
                            self.current_data["lon"] *= -1
                        
                        # RMC에서 fix 정보를 직접 얻지는 않지만, status 'A'는 fix된 것으로 간주할 수 있음
                        # GGA의 fix quality를 더 신뢰하지만, RMC 상태도 참고
                        if self.current_data["fix"] == "0" and status == 'A': # GGA가 fix 0인데 RMC가 A면
                             self.current_data["fix"] = "1" # 일단 1로 (더 나은 fix quality는 GGA에서)


                    # logging.debug(f"[RMC] GPS LOCKED Lat: {self.current_data['lat']} Lon: {self.current_data['lon']}")
                else:
                    # logging.debug("[RMC] NO FIX (Waiting for satellites...)")
                    # RMC에서 V가 나오면 fix를 0으로 설정할 수 있음 (GGA와 조율 필요)
                    # self.current_data["fix"] = "0" # 주석 처리: GGA의 fix를 우선
                    self.current_data["lat"] = 0.0
                    self.current_data["lon"] = 0.0
        except ValueError as e:
            logging.warning(f"GPRMC 파싱 오류 (ValueError): {e} - Fields: {fields}")
        except IndexError as e:
            logging.warning(f"GPRMC 파싱 오류 (IndexError): {e} - Fields: {fields}")

    def poll(self) -> Dict[str, Any]:
        lines_read = 0
        max_lines_to_read_per_poll = 10 # 한 번의 poll에서 너무 많은 라인을 읽지 않도록 제한

        try:
            if not self.ser.is_open:
                logging.error("GPS 시리얼 포트가 닫혀있습니다.")
                return self.current_data # 이전 데이터 반환

            while self.ser.in_waiting > 0 and lines_read < max_lines_to_read_per_poll:
                line_bytes = self.ser.readline()
                lines_read += 1
                try:
                    line = line_bytes.decode('ascii', errors='replace').strip()
                    if not line.startswith('$'):
                        continue

                    fields = line.split(',')
                    if not fields: continue

                    if line.startswith('$GPGGA') and len(fields) > 7:
                        self.last_gga_fields = fields
                        self._parse_gpgga(fields)
                    elif line.startswith('$GPRMC') and len(fields) > 6:
                        self.last_rmc_fields = fields
                        self._parse_gprmc(fields)
                except UnicodeDecodeError:
                    logging.warning(f"GPS 데이터 디코딩 오류: {line_bytes!r}")
                except Exception as e:
                    logging.error(f"GPS 라인 처리 중 예외: {e} - Line: {line_bytes!r}")
            
            # GGA와 RMC 중 어떤 것이 더 최신인지 알 수 없으므로,
            # 가장 최근에 파싱된 데이터를 기반으로 현재 상태를 업데이트하는 것이 좋을 수 있으나,
            # 여기서는 poll() 호출 시점에 누적된 self.current_data를 반환
            return self.current_data

        except serial.SerialException as e:
            logging.error(f"GPS 데이터 읽기 중 시리얼 오류 발생: {e}")
            return self.current_data # 오류 시 이전 데이터 반환
        except Exception as e:
            logging.error(f"GPS poll 중 예기치 않은 오류: {e}")
            return self.current_data


    def is_fixed(self) -> bool:
        # GPGGA의 fix quality가 0보다 크거나, GPRMC 상태가 'A'이면 고정된 것으로 간주
        # GGA fix quality: 0=invalid, 1=GPS fix, 2=DGPS fix, ...
        # RMC status: A=Active/OK, V=Void/Warning
        gga_fix_ok = self.current_data.get("fix", "0") != "0"
        
        # RMC 상태도 추가적으로 확인 가능 (선택 사항)
        # rmc_fix_ok = False
        # if self.last_rmc_fields and len(self.last_rmc_fields) > 2:
        #    rmc_fix_ok = self.last_rmc_fields[2] == 'A'
        # return gga_fix_ok or rmc_fix_ok
        return gga_fix_ok

    def get_max_satellites(self) -> int:
        return self.max_satellites_observed

# ────────── SensorReader ──────────
class SensorReader:
    def __init__(self):
        # MPU 초기화
        try:
            self.m = _RealMPU(MPU_PORT)
        except (serial.SerialException, FileNotFoundError, OSError) as e:
            logging.error(f"필수 MPU 센서({MPU_PORT}) 연결 실패: {e}. 프로그램을 계속할 수 없습니다.")
            raise

        # GPS 초기화 (사용자 선택)
        self.use_real_gps = False
        while True:
            choice = input("실제 GPS를 사용하시겠습니까? (y/n, 기본값 n - Mock GPS): ").strip().lower()
            if choice == 'y':
                self.use_real_gps = True
                try:
                    self.g = _RealGPS(GPS_PORT)
                    logging.info("실제 GPS를 초기화합니다. 위성 신호 탐색을 시작합니다...")
                    self._wait_for_gps_fix() # GPS 고정 대기
                except (serial.SerialException, FileNotFoundError, OSError) as e:
                    logging.error(f"실제 GPS({GPS_PORT}) 연결 실패: {e}. Mock GPS로 대체합니다.")
                    self.g = _MockGPS()
                    self.use_real_gps = False # 실패 시 Mock으로 강제 전환
                break
            elif choice == 'n' or choice == '': # 'n' 또는 그냥 엔터 시 Mock 사용
                self.g = _MockGPS()
                self.use_real_gps = False
                break
            else:
                print("잘못된 입력입니다. 'y' 또는 'n'으로 답해주세요.")
        
        self.last_gps_poll_time = 0
        self.gps_poll_interval = 1.0 # GPS 데이터는 1초에 한 번씩만 poll (선택 사항)


    def _wait_for_gps_fix(self):
        if not self.use_real_gps or not isinstance(self.g, _RealGPS):
            return

        logging.info("GPS 신호 고정 대기 중... (RMC 상태 'A' 또는 GGA Fix Quality > 0)")
        fix_wait_start_time = time.time()
        while not self.g.is_fixed():
            self.g.poll() # 내부적으로 NMEA 문장 계속 읽음
            if time.time() - fix_wait_start_time > 5 : # 5초마다 상태 로깅
                logging.info(f"  GPS 고정 대기 중... 현재 위성 수: {self.g.current_data.get('satellites',0)}, Fix: {self.g.current_data.get('fix', '0')}, Max Obs: {self.g.get_max_satellites()}")
                fix_wait_start_time = time.time() # 로깅 후 타이머 리셋
            time.sleep(0.1) # CPU 사용 줄이기
            if isinstance(self.g, _RealGPS) and not self.g.ser.is_open: # GPS 포트 문제 시 무한루프 방지
                logging.error("GPS 대기 중 GPS 포트가 닫혔습니다. 대기를 중단합니다.")
                break

        if self.g.is_fixed():
            logging.info(f"GPS 신호 고정 완료! 위성 수: {self.g.current_data.get('satellites',0)}, 최대 관측 위성: {self.g.get_max_satellites()}")
        else:
            logging.warning("GPS 신호 고정 대기를 중단했거나 실패했습니다.")


    def get_sensor_data(self) -> Dict[str, Any]:
        d = {"ts": time.time()}
        
        # MPU 데이터 가져오기
        mpu_data: Optional[Dict[str, Any]] = None
        try:
            if hasattr(self, 'm') and self.m is not None:
                 mpu_data = self.m.poll()
            else:
                logging.error("MPU 객체가 초기화되지 않았습니다.")
                mpu_data = {}
        except Exception as e:
            logging.error(f"MPU 데이터 가져오는 중 오류 발생: {e}")
            mpu_data = {}

        d.update(mpu_data or {})

        # GPS 데이터 가져오기 (선택된 GPS 사용)
        # GPS는 너무 자주 poll 할 필요가 없을 수 있으므로 인터벌 적용 (선택 사항)
        current_time = time.time()
        if current_time - self.last_gps_poll_time >= self.gps_poll_interval or not self.last_gps_poll_time :
            gps_raw_data = self.g.poll()
            self.last_gps_poll_time = current_time
        else: # 인터벌 내이면 이전 데이터 사용 (또는 poll() 호출 빈도에 따라 이 로직은 불필요할 수 있음)
            gps_raw_data = self.g.current_data if hasattr(self.g, 'current_data') else self.g.poll()


        # d["gps"] 필드에 GPS 데이터 통합
        # _RealGPS와 _MockGPS가 반환하는 키 이름을 일치시킴 (lat, lon, satellites, fix, altitude)
        d["gps"] = {
            "lat": gps_raw_data.get("lat", 0.0),
            "lon": gps_raw_data.get("lon", 0.0),
            "satellites": gps_raw_data.get("satellites", 0),
            "fix_quality": gps_raw_data.get("fix", "0"), # encoder.py 와 필드명 일치시킬 수 있음
            "altitude": float(gps_raw_data.get("altitude", "0.0")) # float으로 변환
        }
        # 실제 GPS 사용 시 최대 위성 수도 추가 정보로 포함 (선택 사항)
        if self.use_real_gps and isinstance(self.g, _RealGPS) :
            d["gps"]["max_satellites_observed"] = self.g.get_max_satellites()

        return d

# --- run_logger 함수 (기존과 거의 동일, SensorReader 초기화 실패 처리 강화) ---
def run_logger(rate: float = 10.0, target: int = 1000):
    try:
        sr = SensorReader()
    except Exception as e:
        logging.critical(f"SensorReader 초기화 실패로 로거 실행 불가: {e}")
        return

    interval = 1.0 / rate
    # ... (나머지 run_logger 로직은 기존과 동일하게 유지) ...
    ts_start = int(time.time())
    json_path = os.path.join(LOG_DIR, f"log_{ts_start}.jsonl")
    csv_path  = os.path.join(LOG_DIR, f"log_{ts_start}.csv")

    fields = ["ts", "ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw", "lat", "lon", "satellites", "fix_quality", "altitude", "max_satellites_observed"]
    
    try:
        with open(json_path, "w", encoding="utf-8") as fj, \
             open(csv_path, "w", newline="", encoding="utf-8") as fc:
            
            cw = csv.DictWriter(fc, fieldnames=fields, extrasaction='ignore') # extrasaction='ignore' 추가
            cw.writeheader()
            
            logging.info(f"{target}개의 샘플 로깅 시작...")
            for i in range(target):
                t0 = time.time()
                s = sr.get_sensor_data()
                
                fj.write(json.dumps(s) + "\n")
                
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
                    "lon": s.get("gps", {}).get("lon", ""),
                    "satellites": s.get("gps", {}).get("satellites", ""),
                    "fix_quality": s.get("gps", {}).get("fix_quality", ""),
                    "altitude": s.get("gps", {}).get("altitude", ""),
                    "max_satellites_observed": s.get("gps", {}).get("max_satellites_observed", "")
                }
                cw.writerow(row_data)
                
                elapsed_time = time.time() - t0
                sleep_duration = max(0, interval - elapsed_time)
                time.sleep(sleep_duration)
                
                if (i + 1) % (int(rate) * 10) == 0: # rate가 float일 수 있으므로 int로 변환
                    logging.info(f"진행: {i+1}/{target} 샘플 로깅됨.")

    except IOError as e:
        logging.error(f"로그 파일 작업 중 오류 발생 ({json_path} 또는 {csv_path}): {e}")
    except Exception as e:
        logging.error(f"로깅 중 예기치 않은 오류 발생: {e}", exc_info=True)
    
    logging.info(f"로깅 완료: {target} 샘플. 파일: {json_path}, {csv_path}")


if __name__ == "__main__":
    try:
        run_logger(rate=1, target=60) # 테스트를 위해 1Hz, 60초로 변경
    except Exception as e:
        logging.critical(f"run_logger 실행 중 치명적 오류 발생: {e}", exc_info=True)
