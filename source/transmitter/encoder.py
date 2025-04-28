import json
import zlib

def compress_data(data) -> bytes:
    """
    주어진 Python 객체(data)를 JSON 직렬화 후 zlib으로 압축하여 반환합니다.
    추후 신러닝 기반 압축 모델로 교체하기 쉽게 함수 인터페이스는 그대로 유지합니다.
    """
    # 1) JSON 직렬화
    json_str = json.dumps(data)
    byte_data = json_str.encode('utf-8')
    
    # 2) zlib 압축 (level=9는 최대 압축)
    compressed_data = zlib.compress(byte_data, level=9)
    
    # 3) 크기 비교 로그 (압축률 확인용)
    original_size = len(byte_data)
    compressed_size = len(compressed_data)
    
    if original_size > 0:
        compression_ratio = (1 - (compressed_size / original_size)) * 100
    else:
        compression_ratio = 0.0
    
    print("[compress_data] 원본 크기 :", original_size, "bytes")
    print("[compress_data] 압축 후 크기 :", compressed_size, "bytes")
    print("[compress_data] 압축률 : {:.2f}%".format(compression_ratio))
    
    return compressed_data