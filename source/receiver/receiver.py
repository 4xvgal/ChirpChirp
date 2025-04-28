# receiver.py (모듈)
import serial
import time
import datetime
# PacketReassembler 클래스와 예외 클래스를 임포트한다고 가정
from packet_reassembler import PacketReassembler, PacketFormatError, PacketReassemblyError
import decoder            # 사용자 정의 모듈: 압축 해제 및 데이터 복원 기능 수행

# 모듈 레벨 상수 (외부에서 변경 가능하게 하려면 다른 방식 고려)
SERIAL_PORT = '/dev/ttyS0'
BAUD_RATE = 9600
DEFAULT_TIMEOUT = 1 # 시리얼 읽기 타임아웃 기본값

# --- 핵심 수신 및 처리 함수 ---
def receive_loop(port=SERIAL_PORT, baud=BAUD_RATE, serial_timeout=DEFAULT_TIMEOUT):
    """

    Args:
        port (str): 사용할 시리얼 포트 경로.
        baud (int): 통신 속도 (Baud rate).
        serial_timeout (int/float): 시리얼 포트 읽기 타임아웃 (초).

    Returns:
        object: 성공적으로 복원된 센서 데이터 객체.
        None: 메시지 수신/처리 실패 또는 타임아웃 발생 시.
               (KeyboardInterrupt 등 외부 요인 제외)
    
    receiver -> ressembler -> receiver -> decoder

    """
    start_time = time.time()
    reassembler = PacketReassembler() 
    final_sensor_data = None

    # 이 함수가 호출될 때마다 로그 출력 (모듈 사용자에게 정보 제공)
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 시작됨 ({port}, {baud} baud)")
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 데이터 수신 대기 중...")

    ser = None
    try:
        ser = serial.Serial(port, baud, timeout=serial_timeout)
        time.sleep(1) # 포트 안정화 대기

        # TODO: 무한정 대기 대신 타임아웃 로직 추가 고려
        # 예를 들어, 전체 메시지 수신에 대한 타임아웃 설정 가능
        # message_timeout = 30 # 초
        # message_start_time = time.time()

        while True: 

            try:
                if ser.in_waiting > 0:
                    line_bytes = ser.readline()
                    if not line_bytes: # 타임아웃 또는 빈 데이터
                        # 타임아웃이 자주 발생하면 문제일 수 있으므로, 필요시 로깅 또는 처리 추가
                        continue

                    try:
                        line = line_bytes.decode('utf-8', errors='ignore').strip() 
                        if not line:
                            continue

                    except UnicodeDecodeError as ude:
                        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                        print(f"[{timestamp}] Receiver: 오류 - 데이터 디코딩 실패 - {ude}")
                        continue

                    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

                    try:

                        # packet_ressembler.py 
                        reassembled_data = reassembler.process_line(line) 

                        if reassembled_data is not None: ## 재조립된 데이터가 있는 경우


                            print(f"[{timestamp}] Receiver: 재조립 완료 (Reassembler) - {len(reassembled_data)} bytes")
                            
                            # decoder.py
                            sensor_data = decoder.decompress_data(reassembled_data)

                            if sensor_data is None:
                                print(f"[{timestamp}] Receiver: 오류 - 데이터 복원 실패 (Decoder)")
                            else:
                                print(f"[{timestamp}] Receiver: 압축 해제 완료 (Decoder) - 센서 데이터 복원됨.")
                                final_sensor_data = sensor_data


                            # 메시지 처리 완료, 루프 및 함수 종료
                            return final_sensor_data

                    except PacketFormatError as pfe:
                        print(f"[{timestamp}] Receiver: 오류 - 잘못된 패킷 형식 - {pfe}")
                        
                    except PacketReassemblyError as pre:
                        print(f"[{timestamp}] Receiver: 오류 - 재조립 중 문제 - {pre}")

                    except Exception as process_err:
                        print(f"[{timestamp}] Receiver: 오류 - 데이터 처리 중 - {process_err}")

                else:
                    # 읽을 데이터가 없을 때 CPU 사용 방지
                    time.sleep(0.01)


            except Exception as loop_err:
                # 루프 내 예상치 못한 오류 (예: Reassembler 내부 오류 등)
                print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 오류 - 수신 루프 중 오류 발생: {loop_err}")
                time.sleep(0.1) # 잠시 대기 후 계속 시도? 또는 루프 종료 결정 필요

    except serial.SerialException as e:
        print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 시리얼 오류 - 포트({port}) 문제: {e}")
        return None # 시리얼 오류 시 None 반환
    except KeyboardInterrupt:
        print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 사용자에 의해 중단됨.")
        # KeyboardInterrupt는 호출한 쪽으로 전파되도록 re-raise 하거나 None 반환
        raise # 또는 return None
    except Exception as e:
        print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 예상치 못한 오류 발생: {e}")
        return None # 그 외 오류 시 None 반환
    finally:
        if ser and ser.is_open:
            ser.close()
            print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 시리얼 포트 닫힘.")

        elapsed_time = time.time() - start_time
        status = "성공" if final_sensor_data else "실패 또는 중단"
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Receiver: 종료됨 (상태: {status}, 소요시간: {elapsed_time:.2f} 초)")
        # 최종 데이터는 return 문에서 반환됨

 
if __name__ == "__main__":
    print("="*30)
    print(" Receiver Module - Direct Test ")
    print("="*30)
    print(f"테스트: 시리얼 포트 {SERIAL_PORT}에서 메시지 수신 시도...")

    # 테스트 목적으로 함수 직접 호출
    result_data = receive_loop(port=SERIAL_PORT, baud=BAUD_RATE)

    if result_data:
        print("\n[테스트 결과] 데이터 수신 및 복원 성공:")
        # 실제 데이터 형태에 맞게 출력
        print(result_data)
    else:
        print("\n[테스트 결과] 데이터 수신 또는 복원에 실패했습니다.")

    print("\nReceiver Module 테스트 종료.") 
