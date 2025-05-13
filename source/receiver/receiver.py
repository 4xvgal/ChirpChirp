# receiver.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, time, json, datetime, statistics, serial
from collections import deque

# packet_reassembler.py, decoder.py는 같은 디렉토리에 있다고 가정
try:
    from packet_reassembler import PacketReassembler, PacketReassemblyError
    import decoder
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
PORT         = "/dev/ttyAMA0"
BAUD         = 9600
HANDSHAKE_TO = 5.0    # SYN 대기 타임아웃
READ_TO      = 0.05   # 프레임 수신 타임아웃
FRAME_MAX    = 58
DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

SYN = b"SYN\n"
ACK0_FMT = b"ACK%d\n"

import logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

def _log_json(payload: dict, meta: dict):
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def receive_loop():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=HANDSHAKE_TO)
    except serial.SerialException as e:
        logging.error(f"포트 열기 실패: {e}")
        return

    # ── 핸드셰이크 ──
    while True:
        logging.info("SYN 대기 중…")
        line = ser.readline()
        if line == SYN:
            logging.info("SYN 수신, ACK0 전송")
            ser.write(ACK0_FMT % 0)
            ser.flush()
            break

    ser.timeout = READ_TO
    buf = deque()
    reasm = PacketReassembler()
    inter_arrival, pkt_sizes = [], []
    total_bytes = 0
    first_t = last_t = None
    recv_cnt = 0

    try:
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf.extend(chunk)

            while len(buf) >= 1:
                length = buf[0]
                if length < 3 or length > FRAME_MAX:
                    buf.popleft()
                    reasm.reset()
                    inter_arrival.clear(); pkt_sizes.clear()
                    total_bytes = 0; first_t = last_t = None
                    continue
                if len(buf) < 1 + length:
                    break

                buf.popleft()
                frame_bytes = [buf.popleft() for _ in range(length)]
                frame = bytes(frame_bytes)
                seq = frame[0]

                # 프레임마다 ACK<SEQ> 전송
                ack_msg = ACK0_FMT % seq
                ser.write(ack_msg)
                ser.flush()
                logging.debug(f"ACK 전송: {ack_msg!r}")

                # 타이밍 통계
                now = time.time()
                if last_t is not None:
                    inter_arrival.append((now - last_t)*1000)
                last_t = now
                pkt_sizes.append(length+1)
                total_bytes += length+1
                if first_t is None:
                    first_t = now

                try:
                    blob = reasm.process_frame(frame)
                    if blob is None:
                        continue  # 아직 메시지 완성 전

                    recv_cnt += 1
                    payload = decoder.decompress_data(blob)
                    if payload is None:
                        logging.error("디코딩 실패")
                        reasm.reset(); buf.clear()
                        inter_arrival.clear(); pkt_sizes.clear()
                        total_bytes = 0; first_t = last_t = None
                        continue

                    # 출력
                    ts = payload.get("ts", 0.0)
                    human = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    logging.info(f"=== 메시지 #{recv_cnt} 수신 (ts={human}) ===")
                    # (필요하면 payload 내용 출력)

                    # 메타 계산
                    latency = int((now - first_t)*1000) if first_t else 0
                    jitter = statistics.pstdev(inter_arrival) if len(inter_arrival)>1 else 0.0
                    meta = {
                        "bytes_compressed": len(blob),
                        "latency_ms": latency,
                        "jitter_ms": round(jitter,2),
                        "total_bytes_frames": total_bytes,
                        "avg_frame_size": round(sum(pkt_sizes)/len(pkt_sizes),2) if pkt_sizes else 0
                    }
                    logging.info(f"[OK#{recv_cnt}] lat {meta['latency_ms']}ms, jit {meta['jitter_ms']}ms")
                    _log_json(payload, meta)

                    # 다음 메시지 준비
                    reasm.reset()
                    inter_arrival.clear(); pkt_sizes.clear()
                    total_bytes = 0; first_t = last_t = None

                except PacketReassemblyError as e:
                    logging.error(f"재조립 오류: {e}")
                    reasm.reset(); buf.clear()
                    inter_arrival.clear(); pkt_sizes.clear()
                    total_bytes = 0; first_t = last_t = None

    except KeyboardInterrupt:
        logging.info("수신 중단 (KeyboardInterrupt)")
    finally:
        ser.close()
        logging.info("시리얼 포트 닫힘")

if __name__ == "__main__":
    receive_loop()
