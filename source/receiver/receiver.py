# -*- coding: utf-8 -*-
from __future__ import annotations
import os, time, json, datetime, statistics, serial

from packet_reassembler import PacketReassembler, PacketReassemblyError
import decoder

PORT       = "/dev/serial0"
BAUD       = 9600
TIMEOUT_RX = 0.1           # 짧은 바이너리 프레임용
FRAME_MAX  = 58            # 2B 헤더 + 56B payload

SYN_MSG = b"SYN\r\n"
ACK_MSG = b"ACK\n"

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

    expect = recv = 0
    inter  : list[float] = []
    last_t = first_t = None
    total_bytes = 0
    pkt_sizes: list[int] = []

    print(f"[{datetime.datetime.now():%F %T}] Receiver start {PORT}@{BAUD}")

    try:
        while True:
            # ─── SYN/ACK (ASCII) ───
            if ser.in_waiting and ser.peek(1)[:1] == b'S':
                line = ser.readline()
                if line.strip() == b"SYN":
                    ser.write(ACK_MSG)
                continue

            # ─── 바이너리 프레임 ───
            if ser.in_waiting:
                frame = ser.read(ser.in_waiting)   # LoRa 모듈은 프레임 단위로 UART 출력
                if not frame:
                    continue
                if len(frame) > FRAME_MAX or len(frame) < 3:
                    continue                       # 잘못된 길이

                # 통계용
                now = time.time()
                if last_t is not None:
                    inter.append((now - last_t) * 1000)
                last_t = now
                pkt_sizes.append(len(frame))
                total_bytes += len(frame)

                try:
                    data_blob = reasm.process_frame(frame)
                    if data_blob is None:
                        continue  # 아직 다 안 모임

                    # ─── 복원 성공 ───
                    payload = decoder.decompress_data(data_blob)
                    if payload is None:
                        print("[decoder] FAIL"); continue

                    loss_pct = (reasm._total - recv) / reasm._total * 100 if reasm._total else 0
                    meta = {
                        "bytes": len(data_blob),
                        "latency_ms": int((now - first_t)*1000) if first_t else 0,
                        "loss_pct": round(loss_pct, 2),
                        "jitter_ms": round(statistics.pstdev(inter), 2) if len(inter) > 1 else 0,
                        "total_bytes": total_bytes,
                        "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes), 2),
                        "avg_pkt2": round(sum(x*x for x in pkt_sizes)/len(pkt_sizes), 2),
                    }
                    print(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                          f"{meta['bytes']}B, loss {meta['loss_pct']}%, "
                          f"lat {meta['latency_ms']} ms")
                    _log_json(payload, meta)

                    # 새 메시지 통계 초기화
                    expect = recv = 0
                    inter.clear(); pkt_sizes.clear()
                    first_t = last_t = None
                    total_bytes = 0

                except PacketReassemblyError as e:
                    print(f"[ERR] {e}")
                    reasm.reset()

            else:
                time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

if __name__ == "__main__":
    receive_loop()
