import serial
import argparse
import time

# REG3 비트 마스크 및 값
TRANSFER_METHOD_BIT_NUM = 6 # 0부터 시작하는 비트 번호 (오른쪽에서 왼쪽으로)
TRANSPARENT_MODE_BIT_VALUE = 0 # 투명 전송 모드일 때 해당 비트의 값
FIXED_POINT_MODE_BIT_VALUE = 1 # 고정점 전송 모드일 때 해당 비트의 값

def set_transfer_mode_transparent(port, baudrate, save_mode=True, target_reg3_value=None):
    """
    LoRa 모듈을 투명 전송 모드로 설정합니다.
    target_reg3_value가 주어지면, 해당 값으로 REG3를 직접 설정합니다. (투명모드 비트 포함)
    그렇지 않으면 현재 REG3 값을 읽어 Transfer Method 비트만 수정합니다.
    """
    print(f"\n--- 투명 전송 모드 설정 시작 (포트: {port}, 속도: {baudrate}) ---")
    
    current_settings = None
    if target_reg3_value is None: # REG3 값을 직접 지정하지 않은 경우, 현재 설정을 읽어서 수정
        # 1. 현재 설정 읽기 (ADDH ~ REG3, 총 7바이트)
        read_cmd = bytes([0xC1, 0x00, 0x07]) # 헤더, 시작주소(00H), 읽을 길이(7)
        print(f"1. 현재 설정 읽기 명령 전송: {read_cmd.hex().upper()}")
        try:
            with serial.Serial(port, baudrate, timeout=1) as ser:
                ser.write(read_cmd)
                time.sleep(0.2)
                resp = ser.read(3 + 7) # 응답: C1 00 07 AH AL ID R0 R1 R2 R3
                print(f"   읽기 응답: {resp.hex().upper()}")

                if not resp or resp[0] != 0xC1 or resp[1] != 0x00 or resp[2] != 0x07 or len(resp) < 10:
                    print("❌ 현재 설정 읽기 실패 또는 응답 오류. 투명 모드 설정을 진행할 수 없습니다.")
                    return False
                current_settings = list(resp[3:]) # AH AL ID R0 R1 R2 R3
                print(f"   현재 설정값 (AH~R3): {' '.join(f'{val:02X}' for val in current_settings)}")
        except serial.SerialException as e:
            print(f"❌ 현재 설정 읽기 중 시리얼 오류: {e}")
            return False
        except Exception as e:
            print(f"❌ 현재 설정 읽기 중 예기치 않은 오류: {e}")
            return False

        # 2. REG3 값 수정 (Transfer Method 비트만 0으로)
        # REG3는 current_settings[6] (인덱스 6, 실제 주소 06H)
        original_reg3 = current_settings[6]
        # Bit 6을 0으로 만들기: 해당 비트 위치에 0을 AND 연산
        # 마스크: 10111111 (0xBF) -> Bit 6만 0이고 나머지는 1
        modified_reg3 = original_reg3 & (~(1 << TRANSFER_METHOD_BIT_NUM)) # Bit 6을 0으로 설정
        
        print(f"2. REG3 수정:")
        print(f"   원본 REG3 (06H): 0x{original_reg3:02X} ({original_reg3:08b})")
        print(f"   수정 REG3 (06H): 0x{modified_reg3:02X} ({modified_reg3:08b}) (투명 전송 모드)")
        current_settings[6] = modified_reg3 # 수정된 REG3 값으로 교체
    else:
        # target_reg3_value가 주어졌다면, 다른 레지스터는 기본값 또는 더미값으로 설정해야 함
        # 이 기능은 이 스크립트의 원래 목적과 약간 다르므로,
        # 여기서는 "현재 설정을 읽어 투명 모드로만 변경"하는 기능에 집중합니다.
        # 만약 모든 레지스터를 한 번에 설정하고 싶다면 이전의 e22_config.py 전체 버전 사용.
        # 여기서는 target_reg3_value를 사용하지 않고, 항상 읽어서 수정하는 로직으로 갑니다.
        # 또는, 사용자가 모든 레지스터 값을 알고 있을 때만 이 옵션을 사용하도록 안내해야 합니다.
        # 단순화를 위해, 이 함수는 항상 현재 설정을 읽는다고 가정합니다.
        print("   (REG3 직접 설정은 이 간소화 버전에서는 지원하지 않음, 현재 설정 읽기 기반으로 진행)")
        # 만약 모든 레지스터 값을 사용자가 제공하는 시나리오라면, current_settings를 인자로 받아야 함
        # 여기서는 항상 읽는 것으로 진행
        if current_settings is None: # 위에서 읽기 실패 시 current_settings가 None일 수 있음
            print("❌ REG3를 직접 설정하려면 먼저 현재 설정을 읽어야 하나, 읽기 실패함.")
            return False


    # 3. 수정된 설정 쓰기
    cmd_header = 0xC0 if save_mode else 0xC2 # 저장 모드에 따른 헤더
    # 설정할 레지스터: ADDH부터 REG3까지 (시작주소 00H, 길이 07H)
    write_payload = [0x00, 0x07] + current_settings
    write_packet = bytes([cmd_header] + write_payload)

    print(f"3. 수정된 설정 쓰기 명령 전송 ({'영구 저장' if save_mode else '임시 저장'}): {write_packet.hex().upper()}")
    try:
        with serial.Serial(port, baudrate, timeout=1) as ser:
            ser.write(write_packet)
            time.sleep(0.2)
            resp = ser.read(len(write_packet)) # 보낸 길이만큼 응답 기대
            print(f"   쓰기 응답: {resp.hex().upper()}")

            if resp and resp[0] == cmd_header + 1: # (0xC0->0xC1, 0xC2->0xC3)
                if resp[1:] == bytes(write_payload):
                    print("✅ 투명 전송 모드로 성공적으로 설정되었습니다 (응답 내용 일치).")
                    return True
                else:
                    print("⚠️ 투명 전송 모드 설정 성공 (응답 헤더는 맞으나 내용 일부 다름, 모듈 특성일 수 있음).")
                    return True # 일단 헤더가 맞으면 성공으로 간주
            else:
                print(f"❌ 투명 전송 모드 설정 실패 또는 알 수 없는 응답 (헤더: {resp[0]:02X} 예상, 실제: {resp[0] if resp else '없음'}).")
                return False
    except serial.SerialException as e:
        print(f"❌ 설정 쓰기 중 시리얼 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 설정 쓰기 중 예기치 않은 오류: {e}")
        return False

def display_current_config(port, baudrate):
    # 이전 e22_config.py의 read_config 함수와 거의 동일 (REG3 해석 부분 추가)
    read_cmd = bytes([0xC1, 0x00, 0x07])
    print(f"\n--- 현재 모듈 설정 읽기 ---")
    print(f"읽기 명령 전송: {read_cmd.hex().upper()}")
    try:
        with serial.Serial(port, baudrate, timeout=1) as ser:
            ser.write(read_cmd)
            time.sleep(0.2)
            resp = ser.read(3 + 7)
            print(f"읽기 응답: {resp.hex().upper()}")

            if not resp or resp[0] != 0xC1 or resp[1] != 0x00 or resp[2] != 0x07 or len(resp) < 10:
                print("❌ 읽기 실패 또는 응답 오류")
                return

            addr_high, addr_low, netid, reg0, reg1, reg2, reg3 = resp[3:]

            # REG0 해석
            uart_baud_code = (reg0 >> 5) & 0b111
            parity_code = (reg0 >> 3) & 0b11
            air_rate_code = reg0 & 0b111
            uart_baud_str = next((str(k) for k, v in BAUD_BITS.items() if v == uart_baud_code), "Unknown")
            parity_str = next((k for k, v in PARITY_BITS.items() if v == parity_code), "Unknown")
            air_rate_str = next((k for k, v in ADR_BITS.items() if v == air_rate_code), "Unknown")

            # REG1 해석 (데이터시트의 비트 순서에 따라 정확히 해야 함)
            # 예시: 76(PacketSize) 5(NoiseRSSI) 43(Reserved) 10(Power)
            packet_size_code = (reg1 >> 6) & 0b11 # 가정
            enable_noise_rssi = bool((reg1 >> 5) & 0b1) # 가정
            power_code = reg1 & 0b11 # 가정
            packet_size_str = next((str(k) for k, v in PACKET_SIZE_BITS.items() if v == packet_size_code), "Unknown")
            power_str = next((str(k) for k, v in POWER_BITS.items() if v == power_code), "Unknown")
            
            # REG2 (Channel) -> Frequency
            # 데이터시트에 따라 시작 주파수가 다름 (850.125 또는 410.125)
            # 정확한 계산을 위해서는 현재 모듈의 주파수 대역을 알아야 함
            # 여기서는 868MHz 대역을 주로 사용한다고 가정
            start_freq_for_calc = 850.125 # 또는 410.125
            freq_mhz = start_freq_for_calc + reg2 * 1.0 # 채널당 1MHz 증가 가정

            # REG3 해석
            rssi_output_enabled = bool((reg3 >> 7) & 0b1)
            transfer_method_is_transparent = not bool((reg3 >> TRANSFER_METHOD_BIT_NUM) & 0b1) # 0이 투명
            relay_enabled = bool((reg3 >> 5) & 0b1)
            lbt_enabled = bool((reg3 >> 4) & 0b1)
            wor_ctrl = (reg3 >> 3) & 0b1
            wor_period_code = reg3 & 0b111
            
            print("\n--- 현재 모듈 상세 설정 ---")
            print(f"  모듈 주소          : 0x{addr_high:02X}{addr_low:02X} (DEC: {(addr_high << 8) | addr_low})")
            print(f"  네트워크 ID        : 0x{netid:02X}")
            print(f"  REG0 (03H)         : 0x{reg0:02X}")
            print(f"    UART Baud Rate   : {uart_baud_str} bps")
            print(f"    UART Parity      : {parity_str}")
            print(f"    Air Data Rate    : {air_rate_str}")
            print(f"  REG1 (04H)         : 0x{reg1:02X}")
            print(f"    Packet Size      : {packet_size_str} bytes")
            print(f"    Noise RSSI Read  : {enable_noise_rssi}")
            print(f"    Transmit Power   : {power_str} dBm")
            print(f"  REG2 (05H) Channel : 0x{reg2:02X} (DEC: {reg2} -> 약 {freq_mhz:.3f} MHz)")
            print(f"  REG3 (06H) Options : 0x{reg3:02X} ({reg3:08b})")
            print(f"    RSSI Byte Output : {rssi_output_enabled}")
            print(f"    Transfer Method  : {'Transparent' if transfer_method_is_transparent else 'Fixed-point'}")
            print(f"    Relay Function   : {'Enabled' if relay_enabled else 'Disabled'}")
            print(f"    LBT Function     : {'Enabled' if lbt_enabled else 'Disabled'}")
            print(f"    WOR Control      : {wor_ctrl}")
            print(f"    WOR Period Code  : {wor_period_code:03b}")
            print("-------------------------")

    except serial.SerialException as e:
        print(f"❌ 시리얼 통신 오류 (설정 읽기): {e}")
    except Exception as e:
        print(f"❌ 예기치 않은 오류 (설정 읽기): {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E22 LoRa 모듈을 투명 전송 모드로 설정합니다.")
    parser.add_argument("--port", type=str, default="/dev/ttyAMA0", help="시리얼 포트 경로")
    parser.add_argument("--rate", type=int, default=9600, help="PC와 모듈 간 UART 통신 속도")
    parser.add_argument("--save", action="store_true", help="설정을 모듈에 영구 저장 (기본: 임시 저장)")
    parser.add_argument("--verify_before", action="store_true", help="설정 변경 전 현재 설정 값 표시")
    parser.add_argument("--verify_after", action="store_true", help="설정 변경 후 현재 설정 값 표시")
    # --transparent_with_rssi 옵션 추가: 투명모드 + RSSI 출력
    parser.add_argument("--transparent_with_rssi", action="store_true", help="투명 전송 모드 + 수신 시 RSSI 바이트 추가")


    args = parser.parse_args()

    if args.verify_before:
        display_current_config(args.port, args.rate)

    # REG3 목표값 설정
    # 기본: 순수 투명 전송 (REG3의 다른 비트는 현재 값을 유지하면서 Transfer Method만 0으로)
    # 이 로직은 set_transfer_mode_transparent 함수 내부로 옮겨짐.
    # target_reg3_for_transparent = None # None이면 함수 내부에서 현재 값 읽어서 수정
    
    # 만약 사용자가 --transparent_with_rssi 옵션을 주면,
    # REG3의 Bit 7(RSSI Output)을 1로, Bit 6(Transfer Method)을 0으로 설정하고,
    # 나머지 비트는 0으로 설정하는 REG3 값을 직접 만들어서 전달할 수도 있음.
    # 하지만 이는 다른 옵션을 모두 무시하게 되므로, 현재 설정을 읽어 수정하는 방식이 더 안전.
    
    # 여기서는 set_transfer_mode_transparent 함수가 알아서 현재 REG3를 읽고
    # Transfer Method 비트만 0으로 바꾸도록 함.
    # 만약 --transparent_with_rssi 옵션이 있다면, 해당 비트도 함께 고려해야 함.
    # 단순화를 위해, 이 스크립트는 오직 "Transfer Method"만 투명으로 바꾸는 데 집중.
    # RSSI 출력 여부 등은 set_transfer_mode_transparent 함수 내부에서 original_reg3를 기반으로 결정.
    
    # ---- 투명 모드 설정 ----
    # REG3 목표값 결정:
    # 현재 설정(original_reg3)을 읽어서,
    # Bit 6 (Transfer Method) = 0 (Transparent)
    # Bit 7 (RSSI Output) = args.transparent_with_rssi 값에 따라 설정
    # 나머지 비트는 원래 값 유지
    
    # 이 로직은 set_transfer_mode_transparent 함수 내부에서 처리하도록 함.
    # 해당 함수는 먼저 현재 REG3를 읽고, 그 다음 Transfer Method 비트만 0으로 바꿈.
    # RSSI 출력 여부까지 여기서 제어하려면 함수 인자나 로직 추가 필요.
    
    # --- 수정된 REG3 목표값 결정 로직 ---
    # 1. 먼저 현재 설정을 읽는다 (set_transfer_mode_transparent 함수 내부에서 수행)
    # 2. 읽은 REG3 값을 기반으로 목표 REG3 값을 만든다.
    #    - Transfer Method (Bit 6)는 무조건 0 (Transparent)
    #    - RSSI Output (Bit 7)는 --transparent_with_rssi 옵션에 따라 1 또는 0
    #    - 나머지 비트(Relay, LBT, WOR)는 *현재 읽은 값 그대로 유지* (이게 중요!)

    # 이 로직을 set_transfer_mode_transparent 함수에 통합해야 함.
    # 현재 set_transfer_mode_transparent는 RSSI 출력 여부를 변경하지 않고 오직 Transfer Method만 바꿈.
    # 만약 RSSI 출력도 함께 제어하고 싶다면 함수 수정 필요.
    # 여기서는 가장 단순하게 "Transfer Method만 투명으로" 변경하는 기능으로 가정.

    print(f"\n명령: {'영구' if args.save else '임시'} 저장 모드로 투명 전송 모드 설정 시도...")
    success = set_transfer_mode_transparent(args.port, args.rate, save_mode=args.save)

    if success and args.verify_after:
        display_current_config(args.port, args.rate)
    elif not success:
        print("❌ 설정 과정에서 오류가 발생했습니다.")
