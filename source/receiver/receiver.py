# -*- coding: utf-8 -*-
"""
receiver.py — LoRa 리시버 (LEN-SEQ-TOTAL-PAYLOAD)
· readline() 핸드셰이크 → LEN 기반 버퍼 파싱
"""
from __future__ import annotations
import os, time, json, datetime, statistics, serial
from collections import deque

from packet_reassembler import PacketReassembler, PacketReassemblyError
import decoder

# ────────── 설정 ──────────
PORT             = "/dev/serial0"
BAUD             = 9600
HANDSHAKE_TO     = 2.0     # SYN 기다릴 최대 시간
READ_TIMEOUT     = 0.05
FRAME_MAX        = 58      # 2B 헤더 + 56B payload
DATA_DIR         = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

SYN = b"SYN\n"
ACK = b"ACK\n"


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
    ser   = serial.Serial(PORT, BAUD, timeout=HANDSHAKE_TO)
    reasm = PacketReassembler()
    buf   = deque()

    inter: list[float] = []
    pkt_sizes: list[int] = []
    total_bytes = 0
    first_t = last_t = None

    print(f"[{datetime.datetime.now():%F %T}] Receiver start {PORT}@{BAUD}")

    # 1) SYN/ACK 핸드셰이크 (줄 단위)
    while True:
        line = ser.readline()
        if line.strip() == SYN.strip():
            ser.write(ACK)
            break

    # 2) 본격 데이터 수신 (바이너리 LEN 파싱)
    ser.timeout = READ_TIMEOUT
    try:
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf.extend(chunk)

            # LEN 기반 프레임 파싱
            while len(buf) >= 1:
                length = buf[0]
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
                    inter.append((now - last_t) * 1000)
                last_t = now
                pkt_sizes.append(length + 1)
                total_bytes += length + 1

                try:
                    blob = reasm.process_frame(frame)
                    if blob is None:
                        continue

                    payload = decoder.decompress_data(blob)
                    if payload is None:
                        print("[decoder] FAIL")
                        continue

                    latency = int((now - first_t) * 1000) if first_t else 0
                    jitter  = statistics.pstdev(inter) if len(inter) > 1 else 0.0
                    meta = {
                        "bytes": len(blob),
                        "latency_ms": latency,
                        "jitter_ms": round(jitter, 2),
                        "total_bytes": total_bytes,
                        "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes), 2),
                        "avg_pkt2": round(sum(x*x for x in pkt_sizes)/len(pkt_sizes), 2),
                    }
                    print(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                          f"{meta['bytes']}B, lat {latency} ms, jit {meta['jitter_ms']} ms")
                    _log_json(payload, meta)

                    # 새 메시지 통계 초기화
                    buf.clear()
                    inter.clear(); pkt_sizes.clear()
                    total_bytes = 0
                    first_t = last_t = None

                except PacketReassemblyError as e:
                    print(f"[ERR] {e}")
                    reasm.reset()
                    buf.clear()
                    inter.clear(); pkt_sizes.clear()
                    total_bytes = 0
                    first_t = last_t = None

                if first_t is None:
                    first_t = now

            time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    receive_loop()
