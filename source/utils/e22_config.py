import serial
import argparse
import time

# ---------- 설정 가능한 값 매핑 ----------

BAUD_BITS = {
    1200: 0b000,
    2400: 0b001,
    4800: 0b010,
    9600: 0b011,
    19200: 0b100,
    38400: 0b101,
    57600: 0b110,
    115200: 0b111,
}

PARITY_BITS = {
    "8N1": 0b00,
    "8O1": 0b01,
    "8E1": 0b10,
}

ADR_BITS = {
    "0.3k": 0b000,
    "1.2k": 0b001,
    "2.4k": 0b010,
    "4.8k": 0b011,
    "9.6k": 0b100,
    "19.2k": 0b101,
    "38.4k": 0b110,
    "62.5k": 0b111,
}

BAUD_REV = {v: k for k, v in BAUD_BITS.items()}
PARITY_REV = {v: k for k, v in PARITY_BITS.items()}
ADR_REV = {v: k for k, v in ADR_BITS.items()}


def build_reg0(baud, parity, adr):
    return (BAUD_BITS[baud] << 5) | (PARITY_BITS[parity] << 3) | ADR_BITS[adr]


def send_config(cmd_type, addr_high, addr_low, netid, reg0, reg2, port, baudrate):
    base_cmd = []
    if cmd_type == "save":
        base_cmd = [0xC0]
    elif cmd_type == "temp":
        base_cmd = [0xC2]
    elif cmd_type == "wireless":
        base_cmd = [0xCF, 0xCF, 0xC2]
    else:
        raise ValueError("명령 형식 오류: save/temp/wireless 중 하나여야 함")

    packet = bytes(base_cmd + [0x00, 0x05, addr_high, addr_low, netid, reg0, reg2])
    print(f"▶️ 전송 ({cmd_type}): {packet.hex().upper()}")

    with serial.Serial(port, baudrate, timeout=1) as ser:
        ser.write(packet)
        time.sleep(0.1)
        resp = ser.read_all()
        print(f"✅ 응답: {resp.hex().upper()}")

        if resp.startswith(b'\xFF\xFF\xFF'):
            print("❌ 포맷 오류: FF FF FF")
        elif resp.startswith(b'\xC1'):
            print("✅ 설정 성공")
        elif resp.startswith(b'\xCF\xCF\xC1'):
            print("✅ 무선 설정 성공")
        else:
            print("⚠️ 알 수 없는 응답")


def read_config(port, baudrate):
    read_cmd = bytes([0xC1, 0x00, 0x05])
    with serial.Serial(port, baudrate, timeout=1) as ser:
        ser.write(read_cmd)
        time.sleep(0.1)
        resp = ser.read_all()
        print(f"\n 읽기 응답: {resp.hex().upper()}")

        if not resp.startswith(b'\xC1\x00\x05') or len(resp) < 8:
            print("❌ 읽기 실패 또는 응답 오류")
            return

        addr_high = resp[3]
        addr_low = resp[4]
        netid = resp[5]
        reg0 = resp[6]
        reg2 = resp[7]

        baud = BAUD_REV[(reg0 >> 5) & 0b111]
        parity = PARITY_REV[(reg0 >> 3) & 0b11]
        adr = ADR_REV[reg0 & 0b111]

        freq = 850.125 + reg2 * 1.0

        print("현재 설정:")
        print(f"  주소     : 0x{addr_high:02X}{addr_low:02X}")
        print(f"  네트워크 : 0x{netid:02X}")
        print(f"  UART     : {baud} bps")
        print(f"  패리티   : {parity}")
        print(f"  무선속도 : {adr}")
        print(f"  채널     : 0x{reg2:02X} ({reg2} → {freq:.3f} MHz)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E22 설정 전송 + 채널 포함")
    parser.add_argument("--addr", type=lambda x: int(x, 16), default=0x0000)
    parser.add_argument("--netid", type=lambda x: int(x, 16), default=0x00)
    parser.add_argument("--baud", type=int, choices=BAUD_BITS.keys(), default=9600)
    parser.add_argument("--parity", type=str, choices=PARITY_BITS.keys(), default="8N1")
    parser.add_argument("--adr", type=str, choices=ADR_BITS.keys(), default="2.4k")
    parser.add_argument("--channel", type=lambda x: int(x, 16), default=0x32, help="채널 (0x00~0x50)")
    parser.add_argument("--port", type=str, default="/dev/ttyAMA0")
    parser.add_argument("--rate", type=int, default=9600)
    parser.add_argument("--mode", type=str, choices=["save", "temp", "wireless"], default="save")
    parser.add_argument("--verify", action="store_true")

    args = parser.parse_args()

    reg0 = build_reg0(args.baud, args.parity, args.adr)
    addr_high = (args.addr >> 8) & 0xFF
    addr_low = args.addr & 0xFF
    reg2 = args.channel & 0xFF

    print(f"\n📦 REG0 = 0x{reg0:02X}, CH = 0x{reg2:02X}, ADDR = {args.addr:04X}, NETID = {args.netid:02X}")
    send_config(args.mode, addr_high, addr_low, args.netid, reg0, reg2, args.port, args.rate)

    if args.verify:
        read_config(args.port, args.rate)