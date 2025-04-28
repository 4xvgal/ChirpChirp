# session_logger.py
"""
클래스: SessionLogger

`SessionLogger` 클래스는 센서 데이터를 CSV 파일로 기록하는 기능을 담당합니다.
각 세션마다 고유한 파일 이름을 생성하며, 센서 데이터를 구조화된 형식으로 저장합니다。
이 클래스는 로그 파일 초기화、헤더 작성、센서 데이터 행 추가를 자동으로 처리합니다。

주요 기능:
- 로그 파일을 저장할 디렉토리를 자동으로 생성 (기본 디렉토리: "logs").
- 현재 UTC 타임스탬프를 기반으로 고유한 파일 이름 생성.
- 타임스탬프、가속도계(x, y, z)、자이로스코프(x, y, z)、GPS 좌표(위도, 경도) 데이터를 CSV 형식으로 기록.
- CSV 파일에 데이터를 구조화된 방식으로 추가.
"""
import os
import csv

class SessionLogger:
    def __init__(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.filepath = filepath
        self.file = open(filepath, 'w', newline='')
        self.writer = None

    def log(self, data: dict):
        if self.writer is None:
            headers = list(data.keys())
            self.writer = csv.DictWriter(self.file, fieldnames=headers)
            self.writer.writeheader()
        self.writer.writerow(data)

    def close(self):
        if not self.file.closed:
            self.file.close()

