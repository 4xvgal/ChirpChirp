
# 📄 LoRaDataSystem – 모듈 간 데이터 포맷 정의

모든 데이터는 모듈 간 전달 시 **Python dict → JSON 직렬화 → 바이트** 형태로 전송됨.  
단위는 기본적으로 SI 단위 사용.  

---

## 📍 센서 데이터 포맷 (`sensor_reader.py`)

```python
{
 {
    "timestamp": str,  # ISO 8601 format, e.g., "2025-04-11T15:30:00Z"
    "accel": {"x": float, "y": float, "z": float},
    "gyro":  {"x": float, "y": float, "z": float},
    "gps":   {"lat": float, "lon": float}
}
```

---

## 📍 압축 입력 포맷 (`encoder.py`)
- 위 센서 데이터를 JSON 직렬화 → `.encode("utf-8")` 처리한 바이트 스트림
- 예시:

```python
input_bytes = json.dumps(sensor_data).encode("utf-8")
```

---

## 📍 압축 출력 포맷 (`encoder.py`)
- zlib 또는 ML 모델 압축 결과 (`bytes`)
- 압축률 계산을 위해 압축 전/후 크기 비교 가능

---

## 📍 패킷 포맷 (`packetizer.py` → `sender.py`)

```python
{
  "seq": 0,              # 순서 번호 (0부터 시작)
  "total": 3,            # 전체 패킷 수
  "payload": b"..."      # 최대 240바이트 이하 (압축 데이터 일부)
}
```

- 직렬화 없이 binary 상태 유지
- payload는 압축된 바이트의 슬라이스

---

## 📍 수신 패킷 구조 (`receiver.py`)
- 위와 동일한 포맷을 그대로 수신

---

## 📍 복원된 데이터 포맷 (`decoder.py`)
- 압축 해제 후, 원래 센서 dict 형태로 복원됨
- 구조는 `sensor_reader.py`와 동일

---

## 📍 수신 데이터 CSV 저장 포맷 (`data_logger.py`)
| timestamp | accel_x | accel_y | accel_z | gyro_x | gyro_y | gyro_z | lat | lon | alt |
|-----------|---------|---------|---------|--------|--------|--------|-----|-----|-----|

---

## ⛓️ 데이터 흐름 요약

```text
sensor_reader (dict)
   ↓ JSON 직렬화
encoder (bytes 압축)
   ↓ 분할
packetizer (dict 패킷)
   ↓
sender → LoRa 전송
   ↓
receiver → packet_reassembler (bytes)
   ↓
decoder (dict 복원)
   ↓
data_logger (CSV 저장)
```

---

## 🧪 테스트 샘플

```python
sensor_sample = {
  "timestamp": "2025-04-11T15:30:00Z",
  "accel": {"x": 0.01, "y": -0.03, "z": 9.79},
  "gyro": {"x": -0.5, "y": 1.2, "z": 0.0},
  "gps": {"lat": 37.123456, "lon": 127.123456, "alt": 31.2}
}
```

---

## 📌 참고
- 압축 전/후 크기 비교는 `compression_metrics.py`에서 수행
- 버전 동기화는 `model_sync.py`에서 관리
