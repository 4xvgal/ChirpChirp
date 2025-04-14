import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from receiver.decoder import decompress_data
from transmitter.encoder import compress_data



def test_compression_cycle(original_data):
    """
    1) 데이터를 압축하고,
    2) 복원(decompress)한 뒤
    3) 원본과 동일한지 확인
    """
    print("[Test] Original data:", original_data)
    
    # 1) 압축
    compressed = compress_data(original_data)
    
    # 2) (가상) 전송 후 수신된 압축 데이터라고 가정
    received_compressed_data = compressed  # 실제로는 네트워크 전송 등
    
    # 3) 복원
    restored_data = decompress_data(received_compressed_data)
    
    # 결과 확인
    print("[Test] Restored data:", restored_data)
    
    # 4) 원본과 동일한지 검사
    if restored_data == original_data:
        print("[Test] SUCCESS: 복원 결과가 원본과 동일합니다.\n")
    else:
        print("[Test] FAIL: 복원 결과가 원본과 다릅니다.\n")


def main():
    print("=== 압축/복원 테스트 시작 ===\n")
    
    # 간단한 딕셔너리 테스트
    data_dict = {
        "name": "Alice",
        "age": 30,
        "hobbies": ["reading", "cycling", "AI"],
        "active": True
    }
    test_compression_cycle(data_dict)
    
    # 문자열만 있는 경우 테스트
    data_str = "Hello, this is a test string for compression!"
    test_compression_cycle(data_str)
    
    # 복잡한 중첩 구조 테스트
    data_nested = {
        "users": [
            {"id": 1, "roles": ["admin", "editor"]},
            {"id": 2, "roles": ["viewer"]},
        ],
        "metadata": {
            "created": "2025-04-10",
            "tags": ["test", "compression", "LoRa"]
        },
        "values": list(range(50))  # 0~49
    }
    test_compression_cycle(data_nested)
    
    print("=== 압축/복원 테스트 완료 ===")


if __name__ == "__main__":
    main()
