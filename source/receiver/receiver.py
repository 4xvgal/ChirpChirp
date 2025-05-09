# -*- coding: utf-8 -*-
"""
receiver.py — LoRa 리시버 (한 번만 ACK → 연속 프레임 수신)
"""
from __future__ import annotations
import os, time, json, datetime, statistics, serial
from collections import deque

from packet_reassembler import PacketReassembler, PacketReassemblyError
import decoder

# ────────── 설정 ──────────
PORT          = "/dev/serial0"
BAUD          = 9600
HANDSHAKE_TO  = 2.0      # SYN 대기 최대
READ_TO       = 0.05     # 이후 읽기 타임아웃
FRAME_MAX     = 58       # 2B 헤더 + 56B payload
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

    # 1) 최초 핸드셰이크 (한 번만)
    line = ser.readline()
    if line.strip() == SYN.strip():
        ser.write(ACK); ser.flush()
        print("핸드셰이크 OK")
    else:
        print("핸드셰이크 실패, 종료"); ser.close(); return

    # 2) 계속 프레임 수신
    ser.timeout = READ_TO
    buf = deque()
    reasm = PacketReassembler()

    inter_arrival: list[float] = []
    pkt_sizes: list[int] = []
    total_bytes = 0
    first_t = last_t = None

    try:
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf.extend(chunk)

            # LEN 바이트 기반 파싱
            while len(buf) >= 1:
                length = buf[0]
                if length < 3 or length > FRAME_MAX:
                    buf.popleft()
                    continue
                if len(buf) < 1 + length:
                    break
                buf.popleft()
                frame = bytes(buf.popleft() for _ in range(length))

                # 통계
                now = time.time()
                if last_t is not None:
                    inter_arrival.append((now - last_t)*1000)
                last_t = now
                pkt_sizes.append(length+1)
                total_bytes += length+1
                if first_t is None:
                    first_t = now

                # 재조립 & 디코딩
                try:
                    blob = reasm.process_frame(frame)
                    if blob is None:
                        continue

                    payload = decoder.decompress_data(blob)
                    if payload is None:
                        print("[decoder] FAIL")
                        # 클리어 후 다음 메시지 대기
                        reasm.reset()
                        continue

                    latency = int((now - first_t)*1000)
                    jitter  = statistics.pstdev(inter_arrival) if len(inter_arrival)>1 else 0.0
                    meta = {
                        "bytes": len(blob),
                        "latency_ms": latency,
                        "jitter_ms": round(jitter,2),
                        "total_bytes": total_bytes,
                        "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes),2),
                        "avg_pkt2": round(sum(x*x for x in pkt_sizes)/len(pkt_sizes),2),
                    }
                    print(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                          f"{meta['bytes']}B, lat {latency}ms, jit {meta['jitter_ms']}ms")
                    _log_json(payload, meta)

                    # 다음 메시지를 위해 상태 초기화
                    buf.clear()
                    reasm.reset()
                    inter_arrival.clear()
                    pkt_sizes.clear()
                    total_bytes = 0
                    first_t = last_t = None

                except PacketReassemblyError as e:
                    print(f"[ERR] {e}")
                    reasm.reset()
                    buf.clear()
                    inter_arrival.clear()
                    pkt_sizes.clear()
                    total_bytes = 0
                    first_t = last_t = None

            time.sleep(0.005)

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    receive_loop()
