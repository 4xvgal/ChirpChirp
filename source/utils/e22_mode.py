import RPi.GPIO as GPIO
import sys
import time
#usage
#python e22_mode.py config
#python e22_mode.py normal


# BCM 핀 번호 (물리 핀 11, 13)
M0_PIN = 17  # GPIO17
M1_PIN = 27  # GPIO27

MODES = {
    "normal": (GPIO.LOW, GPIO.LOW),   # M0=0, M1=0
    "config": (GPIO.LOW, GPIO.HIGH),  # M0=0, M1=1
}

def set_mode(mode):
    if mode not in MODES:
        print("❌ 지원하지 않는 모드입니다.")
        print("사용법: sudo python3 set_e22_mode.py [normal|config]")
        sys.exit(1)

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(M0_PIN, GPIO.OUT)
    GPIO.setup(M1_PIN, GPIO.OUT)

    m0_val, m1_val = MODES[mode]
    GPIO.output(M0_PIN, m0_val)
    GPIO.output(M1_PIN, m1_val)

    print(f"✅ E22 모드 '{mode}'로 설정 완료 (M0={int(m0_val)}, M1={int(m1_val)})")
    time.sleep(0.1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("사용법: sudo python3 set_e22_mode.py [normal|config]")
        sys.exit(1)

    set_mode(sys.argv[1])