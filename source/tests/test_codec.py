# test_codec.py
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from transmitter.encoder import encoder
from receiver.decoder import decoder

def test_compression_cycle():
    original_data = {
        "name": "TestApp",
        "version": 0.1,
        "features": ["encode", "decode", "compress"],
        "active": False
    }

    print("✅ 원본 데이터:")
    print(original_data)

    # 압축
    compressed = encoder.compress_data(original_data)
    print(f"\n📦 압축된 데이터 크기: {len(compressed)} bytes")

    # 복원
    decompressed = decoder.decompress_data(compressed)
    print("\n🔄 복원된 데이터:")
    print(decompressed)

    # 검증
    if decompressed == original_data:
        print("\n🎉 테스트 성공: 원본과 복원된 데이터가 일치합니다!")
    else:
        print("\n❌ 테스트 실패: 데이터가 일치하지 않습니다.")

if __name__ == "__main__":
    test_compression_cycle()