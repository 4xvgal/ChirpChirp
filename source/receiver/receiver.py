# receiver.py
# -*- coding: utf-8 -*-
"""
LoRa 수신기: 
1) SYN/ACK 핸드셰이크 (매 메시지 시작)
2) LEN-SEQ-TOTAL-PAYLOAD 프레임 수신·재조립
3) 메시지 완료 시 즉시 상태 초기화 → 반복
"""
from __future__ import annotations
import os, time, json, datetime, statistics, serial
from collections import deque

from packet_reassembler import PacketReassembler, PacketReassemblyError
import decoder

# ────────── 설정 ──────────
PORT          = "/dev/serial0"
BAUD          = 9600
HANDSHAKE_TO  = 2.0      # SYN 대기 최대 시간
READ_TO       = 0.05     # 바이너리 수신 타임아웃
FRAME_MAX     = 58       # 2B 헤더 + 56B payload (LEN 바이트 제외)
DATA_DIR      = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

SYN = b"SYN\r\n"
ACK = b"ACK\r\n"


def _log_json(payload: dict, meta: dict):
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": datetime.datetime.utcnow()
                  .isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")


def receive_loop():
    ser = serial.Serial(PORT, BAUD, timeout=HANDSHAKE_TO)
    print(f"[{datetime.datetime.now():%F %T}] Receiver start {PORT}@{BAUD}")

    while True:
        # ─── 1) 핸드셰이크 ───
        # 매 메시지마다 SYN을 기다리고 ACK를 보냄
        print("Waiting for SYN…")
        while True:
            line = ser.readline()
            if line == SYN:
                ser.write(ACK); ser.flush()
                print("Handshake OK")
                break

        # ─── 2) 메시지 수신 준비 ───
        ser.timeout = READ_TO
        buf = deque()
        reasm = PacketReassembler()
        inter_arrival: list[float] = []
        pkt_sizes: list[int] = []
        total_bytes = 0
        first_t = last_t = None

        # ─── 3) 프레임 수신 & 재조립 ───
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf.extend(chunk)

            # LEN-바이트 기반 파싱
            while len(buf) >= 1:
                length = buf[0]
                # 길이 검사
                if length < 3 or length > FRAME_MAX:
                    buf.popleft()
                    continue
                if len(buf) < 1 + length:
                    break
                buf.popleft()  # LEN 제거
                frame = bytes(buf.popleft() for _ in range(length))

                # 통계 업데이트
                now = time.time()
                if last_t is not None:
                    inter_arrival.append((now - last_t) * 1000)
                last_t = now
                pkt_sizes.append(length + 1)
                total_bytes += length + 1
                if first_t is None:
                    first_t = now

                # 재조립 시도
                try:
                    blob = reasm.process_frame(frame)
                    if blob is None:
                        continue  # 아직 모든 패킷이 모이지 않음

                    # 압축 해제 & dict 복원
                    payload = decoder.decompress_data(blob)
                    if payload is None:
                        print("[decoder] FAIL")
                        # 재조립기는 이미 초기화됐으니 다음 메시지로
                        break

                    # 메타 계산
                    latency = int((now - first_t) * 1000)
                    jitter  = statistics.pstdev(inter_arrival) if len(inter_arrival) > 1 else 0.0
                    meta = {
                        "bytes": len(blob),
                        "latency_ms": latency,
                        "jitter_ms": round(jitter, 2),
                        "total_bytes": total_bytes,
                        "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes), 2),
                        "avg_pkt2": round(sum(x*x for x in pkt_sizes)/len(pkt_sizes), 2),
                    }

                    print(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                          f"{meta['bytes']}B, lat {latency}ms, jit {meta['jitter_ms']}ms")

                    _log_json(payload, meta)
                    # 메시지 완료 → 잔여 버퍼·상태 클리어
                    buf.clear()
                    break  # 이 메시지를 끝내고 핸드셰이크 단계로 복귀

                except PacketReassemblyError as e:
                    print(f"[ERR] {e}")
                    reasm.reset()
                    buf.clear()
                    # 통계도 리셋
                    inter_arrival.clear()
                    pkt_sizes.clear()
                    total_bytes = 0
                    first_t = last_t = None
                    break  # 재시작

            # 한 메시지가 끝나면 break to handshake
            if first_t is not None and reasm._total is None:
                # reasm._total는 process_frame이 반환 시 reset됨
                break

            time.sleep(0.005)

    # (종료 시리얼 닫기 생략)


if __name__ == "__main__":
    receive_loop()
