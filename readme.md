# 📡 LoRaDataSystem

> 센서 데이터를 머신러닝 기반으로 압축하고 LoRa 통신을 통해 전송하는 저전력/고효율 데이터 송수신 시스템입니다.  
> **MVP 기준으로는 zlib 압축과 mock 센서 데이터를 사용하여 전체 흐름을 구현합니다.**

---

## 📦 프로젝트 구조

```bash
LoRaDataSystem/
├── transmitter/                # 송신기 측 모듈
│   ├── sensor_reader.py        # 센서 데이터 수집 (시계열)
│   ├── encoder.py              # ML 기반 압축 (Encoding)
│   ├── packetizer.py           # LoRa 전송용 패킷 분할
│   ├── sender.py               # E22 모듈 전송 제어
│   ├── tx_logger.py            # 송신 로그
│   └── e22_config.py           # E22 송신 설정
│
├── receiver/                   # 수신기 측 모듈
│   ├── packet_reassembler.py   # 수신 패킷 재조립
│   ├── decoder.py              # 압축 해제 (Decoding)
│   ├── data_logger.py          # 수신 결과 저장 (CSV)
│   ├── rx_logger.py            # 수신 로그
│   └── e22_config.py           # E22 수신 설정
│
├── common/                     # 공통 모듈
│   ├── model_sync.py           # 모델 동기화 검사
│   ├── compression_metrics.py  # 압축률 계산
│   ├── power_logger.py         # 전력 소비 측정
│   ├── version_manager.py      # 모델 버전 관리
│   └── utils.py                # 유틸 함수
│
└── main.py                     # 전체 실행 진입점 (송/수신 CLI 지원)
```

## 🔁 시스템 데이터 흐름
```
[센서 데이터 수집] 
        ↓
[ML 기반 압축]
        ↓
[LoRa 패킷 분할]
        ↓
[LoRa 송신 (E22)]
        ↓
[LoRa 수신 (E22)]
        ↓
[패킷 재조립]
        ↓
[압축 해제 (Decoding)]
        ↓
[CSV 저장 (수신 완료)]
```


