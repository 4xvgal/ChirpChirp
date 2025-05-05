
# ------------------------------------------------------------
# ㆍ패킷 손실률(loss_pct)   ㆍ지연(latency_ms)   ㆍ지터(jitter_ms)
# ㆍlink_score(0~100)       ㆍ원본 바이트(bytes)
# ㆍ총 수신 바이트(total_bytes) ㆍ평균 패킷 크기(avg_pkt)
# ㆍ패킷 크기² 평균(avg_pkt2)
# ------------------------------------------------------------
from __future__ import annotations
import os, time, json, datetime, statistics, serial
from packet_reassembler import (
    PacketReassembler, PacketFormatError, PacketReassemblyError
)
import decoder

SERIAL_PORT = '/dev/serial0'
BAUD_RATE   = 9600
TIMEOUT     = 1

SYN_MSG = "SYN"
ACK_MSG = "ACK"

DATA_DIR = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)


def _log_json(payload: dict, meta: dict):
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": datetime.datetime.utcnow()
                  .isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def _link_score(loss_pct: float, latency_ms: int, jitter_ms: float) -> int:
    """간단 스코어: 100 – 손실(%) – 지연/10 – 지터/10 (0~100)"""
    score = 100 - loss_pct - latency_ms/10 - jitter_ms/10
    return int(max(0, min(100, score)))

def receive_loop():
    ser   = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=TIMEOUT)
    reasm = PacketReassembler()

    cur_id: int | None = None
    expect = recv = 0
    first_t: float | None = None

    pkt_sizes: list[int] = []
    inter_arrival: list[float] = []
    last_pkt_time: float | None = None
    total_bytes = 0

    print(f"[{datetime.datetime.now():%F %T}] Receiver start {SERIAL_PORT}@{BAUD_RATE}")

    try:
        last_print = time.time()
        while True:
            if ser.in_waiting:
                raw = ser.readline()
                if not raw:
                    continue
                try:
                    line = raw.decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                except UnicodeDecodeError:
                    continue

                # SYN 핸드셰이크
                if line == SYN_MSG:
                    ser.write((ACK_MSG + "\n").encode())
                    last_print = time.time()
                    continue

                # 첫 패킷 → 메시지 메타 초기화
                try:
                    hdr = json.loads(line)
                    if isinstance(hdr, dict) and hdr.get("seq") == 1:
                        cur_id  = hdr.get("id")
                        expect  = hdr.get("total", 0)
                        recv    = 0
                        first_t = time.time()
                        pkt_sizes.clear()
                        inter_arrival.clear()
                        total_bytes = 0
                        last_pkt_time = None
                except json.JSONDecodeError:
                    pass

                # 인터벌·패킷 크기 기록
                now = time.time()
                if last_pkt_time is not None:
                    inter_arrival.append((now - last_pkt_time)*1000)  # ms
                last_pkt_time = now
                pkt_sizes.append(len(line))
                total_bytes += len(line)
                recv += 1

                ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                try:
                    assembled = reasm.process_line(line)
                    if assembled is None:
                        continue

                    latency_ms = int((time.time() - first_t)*1000) if first_t else 0
                    loss_pct   = (expect - recv) / expect * 100 if expect else 0
                    jitter_ms  = statistics.pstdev(inter_arrival) if len(inter_arrival) > 1 else 0.0
                    meta = {
                        "bytes": len(assembled),
                        "pkts_expected": expect,
                        "pkts_recv": recv,
                        "missing": max(expect - recv, 0),
                        "latency_ms": latency_ms,
                        "loss_pct": round(loss_pct, 2),
                        "jitter_ms": round(jitter_ms, 2),
                        "link_score": _link_score(loss_pct, latency_ms, jitter_ms),
                        "total_bytes": total_bytes,
                        "avg_pkt": round(sum(pkt_sizes)/len(pkt_sizes), 2),
                        "avg_pkt2": round(sum(x*x for x in pkt_sizes)/len(pkt_sizes), 2)
                    }
                   

                    payload = decoder.decompress_data(assembled)
                    if payload:
                        print(f"[{ts}] OK score={meta['link_score']} "
                              f"loss={meta['loss_pct']:.1f}% "
                              f"lat={latency_ms}ms jit={meta['jitter_ms']:.1f}ms")
                        _log_json(payload, meta)
                        ser.write(f"ACK:{cur_id}\n".encode())
                    else:
                        print(f"[{ts}] Decoder FAIL")
                        ser.write(f"NAK:{cur_id}\n".encode())
                    last_print = time.time()

                except (PacketFormatError, PacketReassemblyError) as e:
                    print(f"[{ts}] ERR – {e}")
                    ser.write(f"NAK:{cur_id}\n".encode())

            else:
                if time.time() - last_print > 5:
                    print(f"[{datetime.datetime.now():%F %T}] waiting …")
                    last_print = time.time()
                time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

if __name__ == "__main__":
    receive_loop()
