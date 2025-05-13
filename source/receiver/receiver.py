# receiver.py (수정)
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, time, json, datetime, statistics, serial, struct # struct 추가
from collections import deque

try:
    from packet_reassembler import PacketReassembler, PacketReassemblyError # 수정된 Reassembler
    import decoder
except ImportError as e:
    print(f"모듈 임포트 실패: {e}. 확인하세요.")
    exit(1)

# ────────── 설정 ──────────
PORT         = "/dev/ttyS0" # 예시 포트 (환경에 맞게 수정)
BAUD         = 9600
HANDSHAKE_TIMEOUT = 5.0    # 핸드셰이크 SYN 또는 ACK 대기 타임아웃
FRAME_READ_TIMEOUT = 0.5   # 데이터 프레임의 LEN 필드 읽기 타임아웃
# FRAME_MAX 는 이제 (PKT_ID+SEQ+TOTAL+PAYLOAD_CHUNK)의 최대 길이
# encoder.MAX_PAYLOAD_CHUNK = 54, 헤더 3 -> 57. LEN 필드는 제외.
# FRAME_MAX_CONTENT_LEN = 1(PKT_ID)+1(SEQ)+1(TOTAL)+encoder.MAX_PAYLOAD_CHUNK from encoder
# 여기서는 대략적인 상한값으로 사용.
# LoRa 최대 프레임이 58바이트면, LEN 제외한 내용은 최대 57바이트.
FRAME_MAX_CONTENT_LEN = 57 # LEN 제외한 부분의 최대 길이
MIN_FRAME_CONTENT_LEN = 3  # PKT_ID+SEQ+TOTAL (페이로드 0일때)


DATA_DIR     = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

# 프로토콜 메시지 및 상수 (sender.py와 일치해야 함)
SYN_MSG            = b"SYN\r\n" # 핸드셰이크용 SYN
ACK_TYPE_HANDSHAKE = 0x00      # 핸드셰이크 ACK용 TYPE
ACK_TYPE_DATA      = 0xAA      # 데이터 ACK용 TYPE
ACK_PACKET_LEN     = 3         # ACK 패킷의 고정 길이 (PKT_ID+SEQ+TYPE)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _log_json(payload: dict, meta: dict):
    # ... (기존과 동일)
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds")+"Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")

def _send_ack(s: serial.Serial, pkt_id: int, seq: int, ack_type: int):
    ack_bytes = struct.pack("!BBB", pkt_id, seq, ack_type) # Network Byte Order
    try:
        s.write(ack_bytes)
        s.flush()
        logger.debug(f"ACK 전송: PKT_ID={pkt_id}, SEQ={seq}, TYPE={ack_type:#02x} (데이터: {ack_bytes!r})")
    except Exception as e:
        logger.error(f"ACK 전송 실패 (PKT_ID={pkt_id}, SEQ={seq}): {e}")


def receive_loop():
    try:
        # 핸드셰이크 시에는 readline을 사용할 수 있으므로 timeout을 길게 설정
        ser = serial.Serial(PORT, BAUD, timeout=HANDSHAKE_TIMEOUT)
        ser.inter_byte_timeout = None
    except serial.SerialException as e:
        logger.error(f"포트 열기 실패: {e}")
        return

    # ── 핸드셰이크 ──
    handshake_pkt_id = 0 # 핸드셰이크 ACK에 사용할 PKT_ID (sender와 약속)
    handshake_seq = 0    # 핸드셰이크 ACK에 사용할 SEQ (sender와 약속)
    while True:
        logger.info(f"SYN ('{SYN_MSG!r}') 대기 중...")
        # SYN_MSG가 \n으로 끝나면 readline 가능. 아니면 read로 특정 바이트 수 읽어야 함.
        # SYN_MSG = b"SYN\r\n" 이므로 readline 사용 가능.
        line = ser.readline() 
        if line == SYN_MSG:
            logger.info(f"SYN 수신, 핸드셰이크 ACK (PKT_ID={handshake_pkt_id}, SEQ={handshake_seq}, TYPE={ACK_TYPE_HANDSHAKE:#02x}) 전송")
            _send_ack(ser, handshake_pkt_id, handshake_seq, ACK_TYPE_HANDSHAKE)
            break
        elif not line: # 타임아웃
            logger.warning("핸드셰이크: SYN 대기 시간 초과")
            # 필요시 재시도 로직 또는 종료
        else:
            logger.warning(f"핸드셰이크: 예상치 않은 데이터 수신: {line!r}")
            # 버퍼 비우기 등 처리
            ser.reset_input_buffer()


    # 데이터 프레임 수신 설정
    # ser.timeout = FRAME_READ_TIMEOUT # LEN 필드 읽기용 타임아웃
    # FRAME_READ_TIMEOUT은 첫 1바이트(LEN) 읽을 때만 적용, 나머지는 inter_byte_timeout이 관리
    ser.timeout = 0.05 # read(1) 이 블로킹되는 최대 시간 (매우 짧게)
    ser.inter_byte_timeout = 0.1 # 바이트 간 최대 간격

    buffer = bytearray() # 이전 deque 대신 bytearray 사용 (더 효율적일 수 있음)
    
    # PacketReassembler 인스턴스 생성
    # PKT_ID 단위로 재조립하므로, 현재 reassembler는 하나의 PKT_ID만 처리함.
    # 만약 여러 PKT_ID가 인터리빙되어 들어온다면, PKT_ID별로 reassembler 인스턴스를 관리해야 함.
    # (예: reassemblers = {pkt_id1: PacketReassembler(), pkt_id2: PacketReassembler()})
    # 여기서는 단일 reassembler 사용. PacketReassembler 내부에서 PKT_ID 변경 시 리셋됨.
    reassembler = PacketReassembler()
    
    # 통계용 변수 (메시지 단위로 초기화 필요)
    inter_arrival_times, frame_content_sizes = [], []
    current_message_total_bytes_frames = 0
    current_message_first_frame_time = None
    current_message_last_frame_time = None
    
    received_message_count = 0

    try:
        while True:
            try:
                # LEN 필드 (1바이트) 읽기 시도
                len_byte = ser.read(1)
                if not len_byte:
                    # 타임아웃 (정상적인 유휴 상태일 수 있음)
                    time.sleep(0.01) # CPU 사용 방지
                    continue
                
                frame_content_len = len_byte[0]
                
                # 유효한 LEN 값인지 검사
                if not (MIN_FRAME_CONTENT_LEN <= frame_content_len <= FRAME_MAX_CONTENT_LEN):
                    logger.warning(f"잘못된 LEN 값 수신: {frame_content_len}. 버퍼 초기화 및 동기화 재시도.")
                    # 버퍼에 남아있을 수 있는 불완전 데이터 처리
                    if ser.in_waiting > 0:
                        junk = ser.read(ser.in_waiting)
                        logger.debug(f"  잘못된 LEN 후 버려진 데이터: {junk!r}")
                    # reassembler.reset() # 현재 메시지 재조립 중단
                    # 통계 변수도 리셋
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = current_message_last_frame_time = None
                    continue

                # LEN 만큼 프레임 내용(PKT_ID+SEQ+TOTAL+PAYLOAD) 읽기
                # ser.timeout을 frame_content_len에 맞춰 동적으로 설정할 수도 있으나,
                # inter_byte_timeout이 있으므로 read(N)은 N바이트를 기다림.
                frame_content_bytes = ser.read(frame_content_len)

                if len(frame_content_bytes) != frame_content_len:
                    logger.warning(f"프레임 내용 수신 실패: 기대 {frame_content_len}B, 수신 {len(frame_content_bytes)}B. 데이터: {frame_content_bytes!r}")
                    # 이 경우에도 동기화 문제 가능성. 버퍼 비우기 등.
                    if ser.in_waiting > 0: ser.read(ser.in_waiting)
                    # reassembler.reset()
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = current_message_last_frame_time = None
                    continue
                
                # 수신된 프레임 내용 파싱 (PKT_ID, SEQ는 ACK 전송에 필요)
                # PacketReassembler가 내부적으로 파싱하지만, ACK를 위해 여기서도 필요.
                # 또는 PacketReassembler가 파싱한 값을 반환하도록 수정할 수도 있음.
                # 여기서는 간단히 직접 파싱.
                frame_pkt_id = frame_content_bytes[0]
                frame_seq = frame_content_bytes[1]
                # frame_total = frame_content_bytes[2] # 디버깅용

                logger.debug(f"프레임 수신: LEN={frame_content_len}, PKT_ID={frame_pkt_id}, SEQ={frame_seq}, TOTAL={frame_content_bytes[2]}")

                # 수신한 프레임에 대해 ACK 전송
                _send_ack(ser, frame_pkt_id, frame_seq, ACK_TYPE_DATA)

                # 타이밍 통계 (메시지 단위로 관리)
                now = time.time()
                if reassembler._current_pkt_id is None or reassembler._current_pkt_id != frame_pkt_id : # 새 PKT_ID의 첫 프레임이거나, PKT_ID 변경 시
                    # 이전 메시지 통계가 있다면 처리 (이 로직은 reassembler에서 blob 반환 시점으로 옮기는게 나음)
                    # 여기서는 새 메시지 시작 시 통계 변수 초기화
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = now
                    current_message_last_frame_time = now
                else: # 같은 PKT_ID의 후속 프레임
                    if current_message_last_frame_time is not None:
                         inter_arrival_times.append((now - current_message_last_frame_time) * 1000)
                    current_message_last_frame_time = now
                
                # LEN(1B) + 프레임 내용 길이
                frame_content_sizes.append(1 + frame_content_len) 
                current_message_total_bytes_frames += (1 + frame_content_len)
                if current_message_first_frame_time is None: # (위에서 처리했으므로 사실상 불필요)
                    current_message_first_frame_time = now


                # PacketReassembler로 프레임 처리
                try:
                    complete_blob = reassembler.process_frame(frame_content_bytes)
                    
                    if complete_blob is None: # 아직 메시지 완성 전
                        continue
                    
                    # 메시지 완성!
                    received_message_count += 1
                    
                    # 디코딩
                    payload_dict = decoder.decompress_data(complete_blob)
                    if payload_dict is None:
                        logger.error(f"메시지 #{received_message_count} (PKT_ID: {frame_pkt_id}): 디코딩 실패.")
                        # reassembler는 이미 내부적으로 리셋됨. 통계변수만 리셋.
                        inter_arrival_times.clear(); frame_content_sizes.clear()
                        current_message_total_bytes_frames = 0
                        current_message_first_frame_time = current_message_last_frame_time = None
                        continue

                    # 성공적으로 메시지 수신 및 디코딩 완료
                    # 화면 출력 (기존 코드에서 가져옴)
                    ts = payload_dict.get("ts", 0.0)
                    human_ts = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    accel = payload_dict.get("accel", {})
                    gyro  = payload_dict.get("gyro", {})
                    angle = payload_dict.get("angle", {})
                    gps   = payload_dict.get("gps", {})

                    logger.info(f"=== 메시지 #{received_message_count} (PKT_ID: {frame_pkt_id}) 수신 완료 ===")
                    logger.info(f"  Timestamp: {human_ts} (raw: {ts:.3f})")
                    logger.info(f"  Accel (g): Ax={accel.get('ax','N/A'):.3f}, Ay={accel.get('ay','N/A'):.3f}, Az={accel.get('az','N/A'):.3f}")
                    logger.info(f"  Gyro  (°/s): Gx={gyro.get('gx','N/A'):.1f}, Gy={gyro.get('gy','N/A'):.1f}, Gz={gyro.get('gz','N/A'):.1f}")
                    logger.info(f"  Angle (°): Roll={angle.get('roll','N/A'):.1f}, Pitch={angle.get('pitch','N/A'):.1f}, Yaw={angle.get('yaw','N/A'):.1f}")
                    logger.info(f"  GPS   (°): Lat={gps.get('lat','N/A'):.6f}, Lon={gps.get('lon','N/A'):.6f}")

                    # 메타 데이터 계산
                    # now는 현재 시간 (마지막 프레임 처리 완료 후 시간과는 다름, blob 생성된 시간)
                    # latency는 첫 프레임 수신부터 blob 생성까지의 시간
                    latency_ms = int((time.time() - current_message_first_frame_time) * 1000) if current_message_first_frame_time else 0
                    jitter_ms = statistics.pstdev(inter_arrival_times) if len(inter_arrival_times) > 1 else 0.0
                    avg_frame_size_val = round(sum(frame_content_sizes) / len(frame_content_sizes), 2) if frame_content_sizes else 0

                    meta_data = {
                        "pkt_id": frame_pkt_id, # PKT_ID도 메타데이터에 포함
                        "bytes_compressed": len(complete_blob),
                        "latency_ms": latency_ms,
                        "jitter_ms": round(jitter_ms, 2),
                        "total_bytes_frames": current_message_total_bytes_frames,
                        "avg_frame_size": avg_frame_size_val
                    }
                    logger.info(f"  [OK#{received_message_count} PKT_ID:{frame_pkt_id}] Latency: {meta_data['latency_ms']}ms, Jitter: {meta_data['jitter_ms']}ms")
                    _log_json(payload_dict, meta_data)

                    # 다음 메시지 준비 (통계 변수 리셋)
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = current_message_last_frame_time = None
                    # reassembler는 내부적으로 process_frame 성공 시 reset됨.

                except PacketReassemblyError as e:
                    logger.error(f"재조립 오류 (PKT_ID 관련 문제일 수 있음): {e}")
                    # reassembler.reset() # 내부적으로 오류 시 리셋될 수도 있음, 명시적 호출도 고려
                    inter_arrival_times.clear(); frame_content_sizes.clear()
                    current_message_total_bytes_frames = 0
                    current_message_first_frame_time = current_message_last_frame_time = None
                    # 오류 발생 시 버퍼에 남은 데이터가 문제일 수 있으므로, 입력 버퍼를 비우는 것이 좋을 수 있음
                    if ser.in_waiting > 0:
                        logger.debug(f"  재조립 오류 후 버려진 데이터: {ser.read(ser.in_waiting)!r}")

            except Exception as e_outer: # LEN 읽기 또는 프레임 내용 읽기 등에서 발생한 예외
                logger.error(f"프레임 처리 중 예외 발생: {e_outer}")
                # 시리얼 포트 문제 등일 수 있으므로, 잠시 후 재시도 또는 루프 탈출 고려
                time.sleep(0.1)


    except KeyboardInterrupt:
        logger.info("수신 중단 (KeyboardInterrupt)")
    except Exception as e_global:
        logger.error(f"전역 예외 발생: {e_global}", exc_info=True)
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            logger.info("시리얼 포트 닫힘")

if __name__ == "__main__":
    # logger.setLevel(logging.DEBUG) # 상세 로그 확인 시
    receive_loop()