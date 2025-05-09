# receiver.py
# -*- coding: utf-8 -*-
"""
LoRa 리시버
1) 프로그램 시작 시 SYN 한 줄(readline) 기다림 → ACK 한 번 보냄
2) 그 뒤 LEN-SEQ-TOTAL-PAYLOAD 스트림을 끊김 없이 처리
"""
from __future__ import annotations
import os, time, json, datetime, statistics, serial # json, datetime 추가 확인
from collections import deque

# packet_reassembler.py와 decoder.py가 같은 디렉토리에 있다고 가정
try:
    from packet_reassembler import PacketReassembler, PacketReassemblyError
    import decoder
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. packet_reassembler.py와 decoder.py가 올바른 위치에 있는지 확인하세요.")
    exit(1)


# ────────── 설정 ──────────
PORT         = "/dev/serial0"
BAUD         = 9600
HANDSHAKE_TO = 5.0
READ_TO      = 0.05
FRAME_MAX    = 58
DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

SYN = b"SYN\n"
ACK = b"ACK\n"

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
        logging.info(f"Receiver start {PORT}@{BAUD}")
    except serial.SerialException as e:
        logging.error(f"시리얼 포트 {PORT}를 열 수 없습니다: {e}")
        return

    # ... (핸드셰이크 로직은 변경 없음) ...
    handshake_successful = False
    while not handshake_successful:
        logging.info(f"SYN 대기 중... (기대: {SYN!r})")
        line = ser.readline()
        if not line:
            logging.debug("SYN 대기 타임아웃, 재시도...")
            continue
        logging.info(f"핸드셰이크 데이터 수신: {line!r}")
        if line == SYN:
            logging.info(f"SYN 수신 확인. ACK 전송: {ACK!r}")
            ser.write(ACK)
            ser.flush()
            logging.info("핸드셰이크 성공 (SYN 수신, ACK 발신)")
            handshake_successful = True
        else:
            logging.warning(f"핸드셰이크 중 예상치 못한 데이터 수신: {line!r} (기대: {SYN!r})")
    if not handshake_successful:
        logging.error("핸드셰이크 최종 실패. 종료합니다.")
        ser.close()
        return

    ser.timeout = READ_TO
    buf = deque()
    reasm = PacketReassembler()
    inter_arrival: list[float] = []
    pkt_sizes: list[int] = []
    total_bytes = 0
    first_t = last_t = None
    received_message_count = 0

    try:
        while True:
            bytes_to_read = ser.in_waiting or 1
            chunk = ser.read(bytes_to_read)

            if chunk:
                buf.extend(chunk)
                logging.debug(f"데이터 수신: {chunk!r}, 현재 버퍼: {bytes(buf)!r}")

            while len(buf) >= 1:
                length = buf[0]
                if length < 3 or length > FRAME_MAX: # LEN + SEQ + TOTAL 최소 3바이트
                    logging.warning(f"비정상적인 프레임 길이 감지: {length}. 해당 바이트 버림.")
                    buf.popleft()
                    reasm.reset()
                    inter_arrival.clear(); pkt_sizes.clear(); total_bytes = 0; first_t = last_t = None
                    continue
                if len(buf) < 1 + length: # LEN 바이트 + 실제 프레임 길이
                    break
                
                buf.popleft() # LEN 바이트 제거
                frame_bytes = [buf.popleft() for _ in range(length)]
                frame = bytes(frame_bytes) # SEQ, TOTAL, PAYLOAD 부분
                logging.debug(f"프레임 추출: LEN={length}, FRAME={frame!r}")

                now = time.time()
                if last_t is not None:
                    inter_arrival.append((now - last_t) * 1000)
                last_t = now
                pkt_sizes.append(length + 1) # LEN 포함한 전체 프레임 크기
                total_bytes += length + 1
                if first_t is None:
                    first_t = now

                try:
                    blob = reasm.process_frame(frame) # blob은 압축된 전체 메시지
                    if blob is None:
                        logging.debug("프레임 처리: 아직 전체 패킷 아님")
                        continue
                    
                    received_message_count += 1
                    # logging.info(f"--- 메시지 #{received_message_count} 재조립 완료 (압축된 크기: {len(blob)}B) ---")
                    
                    # decoder.py를 통해 압축 해제 및 파싱하여 딕셔너리 생성
                    payload = decoder.decompress_data(blob)
                    
                    if payload is None:
                        # decoder.py 내부에서 "[decoder] 복원 실패: ..." 로그 출력됨
                        logging.error(f"[receiver] 메시지 #{received_message_count} 데이터 디코딩 실패 (payload is None)")
                        reasm.reset(); buf.clear()
                        inter_arrival.clear(); pkt_sizes.clear(); total_bytes = 0; first_t = last_t = None
                        continue

                    # ────────── 수신된 payload 내용 터미널 출력 ──────────
                    logging.info(f"--- 메시지 #{received_message_count} 수신 데이터 (payload) ---")
                    
                    # 방법 1: JSON 형태로 전체 딕셔너리 출력 (들여쓰기 적용)
                    # logging.info(json.dumps(payload, indent=2, ensure_ascii=False))

                    # 방법 2: 주요 항목을 포맷하여 가독성 있게 출력 (권장)
                    ts_value = payload.get('ts', 0.0) # 기본값 0.0으로 설정 (float이므로)
                    ts_human_readable = datetime.datetime.fromtimestamp(ts_value).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    
                    accel = payload.get('accel', {})
                    gyro = payload.get('gyro', {})
                    angle = payload.get('angle', {})
                    gps = payload.get('gps', {})

                    display_message = []
                    display_message.append(f"  Timestamp: {ts_human_readable} (raw: {ts_value:.3f})") # ts도 소수점 표시
                    display_message.append(f"  Accel (g): Ax={accel.get('ax', 'N/A'):.3f}, Ay={accel.get('ay', 'N/A'):.3f}, Az={accel.get('az', 'N/A'):.3f}")
                    display_message.append(f"  Gyro (°/s): Gx={gyro.get('gx', 'N/A'):.1f}, Gy={gyro.get('gy', 'N/A'):.1f}, Gz={gyro.get('gz', 'N/A'):.1f}")
                    display_message.append(f"  Angle (°): Roll={angle.get('roll', 'N/A'):.1f}, Pitch={angle.get('pitch', 'N/A'):.1f}, Yaw={angle.get('yaw', 'N/A'):.1f}")
                    display_message.append(f"  GPS (°): Lat={gps.get('lat', 'N/A'):.6f}, Lon={gps.get('lon', 'N/A'):.6f}")
                    logging.info("\n".join(display_message)) # 각 줄을 개행으로 연결하여 출력
                    # ─────────────────────────────────────────────────────

                    latency = int((now - first_t) * 1000) if first_t is not None else 0
                    jitter  = statistics.pstdev(inter_arrival) if len(inter_arrival) > 1 else 0.0
                    meta = {
                        "bytes_compressed": len(blob), # 압축된 데이터의 바이트 크기
                        "latency_ms": latency,
                        "jitter_ms": round(jitter, 2),
                        "total_bytes_frames": total_bytes, # 수신된 프레임(LEN+헤더+페이로드)들의 총합
                        "avg_frame_size": round(sum(pkt_sizes)/len(pkt_sizes), 2) if pkt_sizes else 0,
                    }
                    logging.info(f"[{datetime.datetime.now():%H:%M:%S.%f} OK] "
                                 f"Msg#{received_message_count}: {meta['bytes_compressed']}B compressed, "
                                 f"lat {latency}ms, jit {meta['jitter_ms']}ms")
                    _log_json(payload, meta)

                    reasm.reset()
                    inter_arrival.clear(); pkt_sizes.clear(); total_bytes = 0; first_t = last_t = None

                except PacketReassemblyError as e:
                    logging.error(f"패킷 재조립 오류: {e}")
                    reasm.reset(); buf.clear()
                    inter_arrival.clear(); pkt_sizes.clear(); total_bytes = 0; first_t = last_t = None
            
            # time.sleep(0.001)

    except KeyboardInterrupt:
        logging.info("사용자에 의해 중단됨.")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}", exc_info=True)
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            logging.info("시리얼 포트 닫힘.")

if __name__ == "__main__":
    receive_loop()