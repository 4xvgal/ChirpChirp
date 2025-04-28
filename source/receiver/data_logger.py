import csv
import os
from datetime import datetime, timezone

"""
클래스: SessionLogger

`SessionLogger` 클래스는 센서 데이터를 CSV 파일로 기록하는 기능을 담당합니다.
각 세션마다 고유한 파일 이름을 생성하며, 센서 데이터를 구조화된 형식으로 저장합니다.
이 클래스는 로그 파일 초기화, 헤더 작성, 센서 데이터 행 추가를 자동으로 처리합니다.

주요 기능:
- 로그 파일을 저장할 디렉토리를 자동으로 생성 (기본 디렉토리: "logs").
- 현재 UTC 타임스탬프를 기반으로 고유한 파일 이름 생성.
- 타임스탬프, 가속도계(x, y, z), 자이로스코프(x, y, z), GPS 좌표(위도, 경도) 데이터를 CSV 형식으로 기록.
- CSV 파일에 데이터를 구조화된 방식으로 추가.

메서드:
1. `__init__(log_dir="logs")`: 로거를 초기화하고, 로그 디렉토리를 생성하며, 로그 파일을 설정합니다.
2. `_generate_filename()`: 세션 로그 파일의 고유한 파일 이름을 생성합니다.
3. `_init_log_file()`: 로그 파일을 생성하고, 미리 정의된 필드 이름으로 헤더를 작성합니다.
4. `log(data: dict)`: 센서 데이터를 받아 로그 파일에 한 행으로 추가합니다.

사용법:
- `SessionLogger` 클래스를 임포트하고 인스턴스를 생성합니다.
- `log` 메서드를 사용하여 딕셔너리 형식의 센서 데이터를 기록합니다.
- `__main__` 블록에서 센서 데이터 리더와 함께 동작하도록 설계되었습니다.

테스트 코드 : source/tests/test_data_logger.py
예제:
    from transmitter.sensor_reader import SensorReader
    reader = SensorReader()
    logger = SessionLogger()

    for _ in range(10):
        data = reader.get_sensor_data()
        logger.log(data)
"""

class SessionLogger:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, self._generate_filename())
        self.fieldnames = [
            "timestamp",
            "accel_x", "accel_y", "accel_z",
            "gyro_x", "gyro_y", "gyro_z",
            "gps_lat", "gps_lon"
        ]
        self._init_log_file()

    def _generate_filename(self):
        now = datetime.now(timezone.utc)
        return f"session_{now.strftime('%Y%m%d_%H%M%S')}.csv"

    def _init_log_file(self):
        with open(self.log_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            writer.writeheader()

    def log(self, data: dict):
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "accel_x": data["accel"]["x"],
            "accel_y": data["accel"]["y"],
            "accel_z": data["accel"]["z"],
            "gyro_x": data["gyro"]["x"],
            "gyro_y": data["gyro"]["y"],
            "gyro_z": data["gyro"]["z"],
            "gps_lat": data["gps"]["lat"],
            "gps_lon": data["gps"]["lon"]
        }
        with open(self.log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            writer.writerow(row)

'''
if __name__ == "__main__":
    from transmitter.sensor_reader import SensorReader
    reader = SensorReader()
    logger = SessionLogger()

    for _ in range(10):
        data = reader.get_sensor_data()
        logger.log(data)


'''