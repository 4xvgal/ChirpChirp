from sx126x import sx126x

# --- 상수 정의 (필요시 이 값들만 수정하면 됨) ---
SERIAL_PORT   = "/dev/ttyAMA0"
FREQ_MHZ      = 868
NODE_ADDR     = 0x0000
POWER_DBM     = 22         # 고정 출력값
ENABLE_RSSI   = True       # True면 패킷 수신시 RSSI 출력

# 고정 파라미터
AIR_SPEED     = 300
NET_ID        = 0
BUFFER_SIZE   = 240
CRYPT_KEY     = 0x0000
RELAY         = False
LBT           = False
WOR           = False

# --- LoRa 모듈 초기화 및 설정 ---
lora = sx126x(
    serial_num=SERIAL_PORT,
    freq=FREQ_MHZ,
    addr=NODE_ADDR,
    power=POWER_DBM,
    rssi=ENABLE_RSSI,
    air_speed=AIR_SPEED,
    net_id=NET_ID,
    buffer_size=BUFFER_SIZE,
    crypt=CRYPT_KEY,
    relay=RELAY,
    lbt=LBT,
    wor=WOR
)

print("LoRa 모듈이 성공적으로 설정되었습니다.")

# --- 현재 설정값 출력 ---
lora.get_settings()