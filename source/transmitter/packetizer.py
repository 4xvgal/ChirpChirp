import math
from typing import List

def split_into_packets(data: bytes, max_size: int = 50) -> List[dict]:
    """
    바이트 데이터를 LoRa 전송에 적합하도록 max_size 단위로 분할하고
    각 패킷에 순서 정보와 전체 개수 포함
    
    반환 형식:
    [
        {"seq": 1, "total": 5, "payload": b"..."},
        {"seq": 2, "total": 5, "payload": b"..."},
        ...
    ]
    """
    if max_size <= 0:
        raise ValueError("max_size는 0보다 커야 합니다.")

    total_packets = math.ceil(len(data) / max_size)
    packets = []

    for i in range(total_packets):
        start = i * max_size
        end = start + max_size
        payload = data[start:end]
        packet = {
            "seq": i + 1,          # seq는 1부터 시작
            "total": total_packets,
            "payload": payload     # bytes 형태 그대로 반환
        }

        if len(payload) > max_size:
            raise ValueError(f"{i+1}번째 패킷의 payload가 max_size({max_size})를 초과합니다.")

        packets.append(packet)

    return packets

'''# 테스트 코드 (직접 실행 시)
if __name__ == "__main__":
    import zlib

    dummy_text = b"The quick brown fox jumps over the lazy dog" * 5
    compressed = zlib.compress(dummy_text)

    print(f"압축된 데이터 크기: {len(compressed)} bytes")

    split_packets = split_into_packets(compressed, max_size=50)

    for p in split_packets:
        print({
            "seq": p["seq"],
            "total": p["total"],
            "payload_len": len(p["payload"]),
            "payload": p["payload"]
        })
    '''