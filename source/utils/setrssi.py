import serial
import time
import argparse

#usage
#python setrssi.py --port /dev/ttyAMA0 --enable
#python setrssi.py --port /dev/ttyAMA0 --disable

# --- LoRa 모듈 레지스터 및 비트 정의 (제공된 매뉴얼 기준) ---
# REG1 (인덱스 4, 주소 04H)
REG1_OFFSET = 4
REG1_AMBIENT_NOISE_ENABLE_BIT = (1 << 5)  # Bit 5: Enable ambient noise

# REG3 (인덱스 6, 주소 06H)
REG3_OFFSET = 6
REG3_RSSI_BYTE_ENABLE_BIT = (1 << 7)      # Bit 7: Enable RSSI byte

# --- 설정 읽기/쓰기 명령 코드 ---
# 모듈 설정 읽기 (파라미터 7바이트: 00H~06H)
CMD_READ_CONFIG = bytes([0xC1, 0x00, 0x07])
# 모듈 설정 쓰기 (파라미터 7바이트: 00H~06H, 영구 저장)
CMD_WRITE_CONFIG_HEADER = bytes([0xC0, 0x00, 0x07])


def read_current_full_config(ser):
    """
    LoRa 모듈의 현재 전체 설정 파라미터 (7 바이트: 00H~06H)를 읽습니다.
    반환값: 성공 시 7바이트 설정 데이터 (bytes), 실패 시 None
    """
    ser.write(CMD_READ_CONFIG)
    time.sleep(0.1)  # 모듈 응답 대기
    resp = ser.read_all()
    print(f"📥 전체 설정 읽기 응답: {resp.hex().upper()}")

    # 예상 응답: C1 00 07 XX XX XX XX XX XX XX (헤더 3 + 데이터 7 = 총 10 바이트)
    if not resp.startswith(CMD_READ_CONFIG) or len(resp) < 10:
        print("❌ 전체 설정 읽기 실패 또는 응답 길이 오류")
        return None

    return resp[3:10]  # 실제 설정 데이터 7바이트 반환

def write_modified_config(ser, current_params, rssi_enable):
    """
    읽어온 현재 설정을 기반으로 RSSI 관련 비트만 수정한 후, 전체 설정을 모듈에 다시 씁니다.
    current_params: 읽어온 7바이트 설정 데이터 (bytes)
    rssi_enable: True이면 RSSI 활성화, False이면 비활성화
    """
    if not isinstance(current_params, bytes) or len(current_params) != 7:
        print("❌ 내부 오류: 잘못된 파라미터 전달")
        return False

    # bytes를 list of int로 변환하여 수정 용이하게 함
    params_list = list(current_params)

    # REG1 (Ambient Noise) 수정
    reg1_original = params_list[REG1_OFFSET]
    if rssi_enable:
        params_list[REG1_OFFSET] = reg1_original | REG1_AMBIENT_NOISE_ENABLE_BIT
    else:
        params_list[REG1_OFFSET] = reg1_original & (~REG1_AMBIENT_NOISE_ENABLE_BIT)

    print(f"REG1 (04H) 변경: {reg1_original:02X} -> {params_list[REG1_OFFSET]:02X} "
          f"(Ambient Noise {'활성화' if rssi_enable else '비활성화'})")

    # REG3 (RSSI Byte) 수정
    reg3_original = params_list[REG3_OFFSET]
    if rssi_enable:
        params_list[REG3_OFFSET] = reg3_original | REG3_RSSI_BYTE_ENABLE_BIT
    else:
        params_list[REG3_OFFSET] = reg3_original & (~REG3_RSSI_BYTE_ENABLE_BIT)

    print(f"REG3 (06H) 변경: {reg3_original:02X} -> {params_list[REG3_OFFSET]:02X} "
          f"(RSSI Byte {'활성화' if rssi_enable else '비활성화'})")

    # 수정된 파라미터로 쓰기 패킷 생성
    write_packet_data = bytes(params_list)
    full_write_packet = CMD_WRITE_CONFIG_HEADER + write_packet_data

    print(f"📡 RSSI {'활성화' if rssi_enable else '비활성화'} (다른 설정 유지) 전송: {full_write_packet.hex().upper()}")
    ser.write(full_write_packet)
    time.sleep(0.2)  # 설정 저장 및 모듈 응답 대기 (모듈에 따라 조정 필요)
    resp = ser.read_all()
    print(f"✅ 쓰기 응답 (Raw): {resp.hex().upper()}")

    # 응답 확인: Ebyte 모듈 등은 C1 00 07 + 쓴 값을 그대로 반환함
    expected_response = CMD_READ_CONFIG + write_packet_data # 쓰기 성공시 읽기명령 헤더 + 쓴값
    if resp == expected_response:
        print(f"✅ 설정 성공 및 확인: RSSI 기능이 {'켜졌습니다' if rssi_enable else '꺼졌습니다'}. 다른 설정은 유지되었습니다.")
        return True
    elif resp.startswith(CMD_READ_CONFIG[:2]): # C1 00 으로 시작하는 응답
        print(f"✅ 설정 명령 응답 수신 (내용 확인 필요): RSSI {'켜짐' if rssi_enable else '꺼짐'} 시도됨.")
        # 여기서 resp[3:10] == write_packet_data 인지 추가 확인 가능
        return True
    else:
        print("⚠️ 쓰기 응답이 비정상적이거나 무응답입니다. 설정이 적용되지 않았을 수 있습니다.")
        return False

def toggle_rssi_safely(port, baudrate, rssi_enable):
    """
    지정된 시리얼 포트를 통해 LoRa 모듈의 RSSI 기능만 안전하게 켜거나 끕니다.
    """
    try:
        with serial.Serial(port, baudrate, timeout=1) as ser:
            print(f"\n--- {port} @ {baudrate}bps LoRa 모듈 RSSI 설정 시작 ---")

            # 1. 현재 모듈의 전체 설정 읽기
            print("\n[단계 1] 현재 모듈의 전체 설정을 읽습니다...")
            current_config_bytes = read_current_full_config(ser)

            if not current_config_bytes:
                print("현재 설정을 읽을 수 없어 RSSI 변경 작업을 중단합니다.")
                return

            print("읽어온 현재 설정 값 (Hex):")
            print(f"  ADDH(00H): {current_config_bytes[0]:02X}, ADDL(01H): {current_config_bytes[1]:02X}, NETID(02H): {current_config_bytes[2]:02X}")
            print(f"  REG0(03H): {current_config_bytes[3]:02X} (UART, Parity, AirSpeed)")
            print(f"  REG1(04H): {current_config_bytes[4]:02X} (PacketSize, AmbientNoise, Power)")
            print(f"  REG2(05H): {current_config_bytes[5]:02X} (Channel)")
            print(f"  REG3(06H): {current_config_bytes[6]:02X} (RSSI_Byte, Transfer, Relay, LBT, WOR)")

            # 2. 읽어온 설정을 기반으로 RSSI 관련 비트만 수정하여 쓰기
            action = "활성화" if rssi_enable else "비활성화"
            print(f"\n[단계 2] RSSI 기능 {action} (다른 설정은 유지) 시도...")
            success = write_modified_config(ser, current_config_bytes, rssi_enable)

            if not success:
                print(f"RSSI 기능 {action}에 실패했습니다.")
                return

            # 3. (선택 사항) 변경 후 설정 다시 읽어 최종 확인
            print("\n[단계 3] 변경된 설정 확인을 위해 다시 읽습니다...")
            time.sleep(0.5) # 설정 적용 및 안정화 대기
            final_config_bytes = read_current_full_config(ser)

            if final_config_bytes:
                print("최종 확인된 설정 값 (REG1, REG3만 표시):")
                print(f"  REG1(04H): {final_config_bytes[REG1_OFFSET]:02X}")
                print(f"  REG3(06H): {final_config_bytes[REG3_OFFSET]:02X}")

                # 예상되는 REG1, REG3 값 계산
                expected_reg1 = current_config_bytes[REG1_OFFSET]
                expected_reg3 = current_config_bytes[REG3_OFFSET]
                if rssi_enable:
                    expected_reg1 |= REG1_AMBIENT_NOISE_ENABLE_BIT
                    expected_reg3 |= REG3_RSSI_BYTE_ENABLE_BIT
                else:
                    expected_reg1 &= ~REG1_AMBIENT_NOISE_ENABLE_BIT
                    expected_reg3 &= ~REG3_RSSI_BYTE_ENABLE_BIT

                if (final_config_bytes[REG1_OFFSET] == expected_reg1 and
                    final_config_bytes[REG3_OFFSET] == expected_reg3):
                    print("✅ 최종 확인: REG1, REG3 값이 의도한 대로 변경되었습니다.")
                else:
                    print("⚠️ 최종 확인: REG1 또는 REG3 값이 예상과 다릅니다. 로그를 확인해주세요.")
            else:
                print("최종 설정 확인에 실패했습니다.")

            print(f"\n--- LoRa 모듈 RSSI 설정 완료 ({action}) ---")

    except serial.SerialException as e:
        print(f"❌ 시리얼 포트 오류: {e}")
    except Exception as e:
        print(f"❌ 예기치 않은 오류 발생: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LoRa 모듈의 RSSI 기능만 안전하게 켜거나 끕니다 (다른 설정 유지).",
        formatter_class=argparse.RawTextHelpFormatter # 줄바꿈 유지
    )
    parser.add_argument(
        "--port",
        type=str,
        required=True,
        help="시리얼 포트 이름 (예: /dev/ttyUSB0 또는 COM3)"
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=9600,
        help="보드레이트 (기본값: 9600)"
    )

    rssi_group = parser.add_mutually_exclusive_group(required=True)
    rssi_group.add_argument(
        "--enable",
        action="store_true",
        help="RSSI 관련 기능 (Ambient Noise 및 RSSI Byte)을 켭니다."
    )
    rssi_group.add_argument(
        "--disable",
        action="store_true",
        help="RSSI 관련 기능 (Ambient Noise 및 RSSI Byte)을 끕니다."
    )

    args = parser.parse_args()

    toggle_rssi_safely(args.port, args.rate, rssi_enable=args.enable)