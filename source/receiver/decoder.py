import json
import zlib

def decompress_data(data: bytes) -> dict:
    """
    압축된 바이너리 데이터를 zlib으로 해제 후,
    JSON 형식으로 파싱하여 dict 형태의 원본 데이터를 반환합니다.
    
    복원 실패 시 예외를 처리하고 None을 반환하여 fallback 합니다.
    """
    try:
        # 1) zlib 해제
        decompressed_bytes = zlib.decompress(data)
        
        # 2) JSON 디코딩
        json_str = decompressed_bytes.decode('utf-8')
        original_data = json.loads(json_str)
        
        return original_data
    except Exception as e:
        # 복원 실패 시 예외 로깅 및 fallback 처리
        print(f"[decompress_data] 복원 실패: {e}")
        return None
