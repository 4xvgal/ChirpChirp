# -*- coding: utf-8 -*-
"""
receiver.py – LEN‑SEQ‑TOTAL‑PAYLOAD 수신
"""
from __future__ import annotations
import os, time, json, datetime, statistics, serial
from collections import deque

from packet_reassembler import PacketReassembler, PacketReassemblyError
import decoder

PORT        = "/dev/serial0"
BAUD        = 9600
TIMEOUT_RX  = 0.05

FRAME_MAX   = 58        # 2B 헤더 + 56B payload
LEN_MAX     = FRAME_MAX

SYN   = b"SYN"
ACK   = b"ACK\n"

DATA_DIR = "data/raw"; os.makedirs(DATA_DIR, exist_ok=True)

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
    ser   = serial.Serial(PORT, BAUD, timeout=TIMEOUT_RX)
    reasm = PacketReassembler()

    buf   = deque()                 # 직렬 수신 버퍼
    pkt_sizes: list[int] = []
    inter: list[float] = []

    first_t = last_t = None
    total_bytes = 0

    print(f"[{datetime.datetime.now():%F %T}] Receiver start {PORT}@{BAUD}")

    try:
        handshake_done = False
        while True:
            # ── 읽어 들이기 ──
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf.extend(chunk)

            # ── 핸드셰이크 처리 ──
            if not handshake_done:
                if b"SYN" in bytes(buf):
                    ser.readline()          # SYN\r\n 소거
                    ser.write(ACK)
                    handshake_done = True
                    buf.clear()
                continue

            # ── LEN 기반 프레임 파싱 ──
            while True:
                if len(buf) < 1:
                    break
                frame_len = buf[0]
                if frame_len == 0 or frame_len > LEN_MAX:
                    buf.popleft(); continue   # 잘못된 LEN, 버림
                if len(buf) < 1 + frame_len:
                    break                     # 아직 다 안 옴
                buf.popleft()                 # LEN 바이트 삭제
                frame = bytes(buf.popleft() for _ in range(frame_len))

                now = time.time()
                if last_t is not None:
                    inter.append((now - last_t) * 1000)
                last_t = now
                pkt_sizes.append(frame_len + 1)   # LEN 포함
                total_bytes += frame_len + 1

                try:
                    blob = reasm.process_frame(frame)
                    if blob is None:
                        continue

                    payload = decoder.decompress_data(blob)
                    if payload is None:
                        print("[decoder] FAIL"); continue

                    jitter = statistics.pstdev(inter) if len(inter) > 1 else 0.0
                    latency = int((now - first_t)*1000) if first_t else 0
                    meta = {
                        "bytes": len(blob),
                        "latency_ms": latency,
                        "jitter_ms": round(jitter, 2),
                        "total_bytes": total_bytes,
                        "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes), 2),
                    }
                    print(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                          f"{meta['bytes']}B, lat {latency}ms, jit {meta['jitter_ms']}ms")
                    _log_json(payload, meta)

                    # 통계 리셋
                    pkt_sizes.clear(); inter.clear()
                    total_bytes = 0
                    first_t = last_t = None

                except PacketReassemblyError as e:
                    print(f"[ERR] {e}")
                    reasm.reset()
                    pkt_sizes.clear(); inter.clear()
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
