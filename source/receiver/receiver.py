# -*- coding: utf-8 -*-
"""
receiver.py – 2 B 헤더 + payload 프레임 수신
· 프레임은 LF('\n')로 구분됨
"""
from __future__ import annotations
import os, time, json, datetime, statistics, serial
from packet_reassembler import PacketReassembler, PacketReassemblyError
import decoder

PORT        = "/dev/serial0"
BAUD        = 9600
TIMEOUT_RX  = 0.1
FRAME_MAX   = 58               # 2B 헤더 + 56B payload

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

    inter: list[float] = []
    pkt_sizes: list[int] = []
    total_bytes = 0
    first_t = last_t = None

    print(f"[{datetime.datetime.now():%F %T}] Receiver start {PORT}@{BAUD}")

    try:
        while True:
            raw = ser.readline()              # LF 기준
            if not raw:
                time.sleep(0.01); continue

            # ── SYN/ACK ──
            if raw.strip() == SYN:
                ser.write(ACK)
                continue

            frame = raw.rstrip(b"\r\n")       # LF 제거
            if len(frame) < 3 or len(frame) > FRAME_MAX:
                continue                      # 길이 오류

            now = time.time()
            if last_t is not None:
                inter.append((now - last_t) * 1000)  # ms
            last_t = now
            pkt_sizes.append(len(frame))
            total_bytes += len(frame)

            try:
                blob = reasm.process_frame(frame)
                if blob is None:
                    continue  # 아직 미완

                payload = decoder.decompress_data(blob)
                if payload is None:
                    print("[decoder] FAIL"); continue

                jitter   = statistics.pstdev(inter) if len(inter) > 1 else 0.0
                latency  = int((now - first_t)*1000) if first_t else 0
                meta = {
                    "bytes": len(blob),
                    "latency_ms": latency,
                    "jitter_ms": round(jitter, 2),
                    "total_bytes": total_bytes,
                    "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes), 2),
                    "avg_pkt2": round(sum(x*x for x in pkt_sizes)/len(pkt_sizes), 2),
                }
                print(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                      f"{meta['bytes']}B, lat {latency} ms, jit {meta['jitter_ms']} ms")
                _log_json(payload, meta)

                # 새 메시지 통계 초기화
                inter.clear(); pkt_sizes.clear()
                total_bytes = 0
                first_t = last_t = None

            except PacketReassemblyError as e:
                print(f"[ERR] {e}")
                reasm.reset()
                inter.clear(); pkt_sizes.clear()
                total_bytes = 0
                first_t = last_t = None

            if first_t is None:
                first_t = now

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

if __name__ == "__main__":
    receive_loop()
