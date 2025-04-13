import zlib
import json
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class encoder:
    @staticmethod
    def compress_data(data: dict) -> bytes:
        """
        입력 데이터를 JSON 직렬화한 후 zlib으로 압축하여 반환한다.
        추후 AI 모델 기반 인코딩으로 아래 '압축 처리 구간'을 교체하면 된다.
        
        Args:
            data (dict): 압축할 데이터

        Returns:
            bytes: 압축된 데이터
        """
        # JSON 직렬화 및 UTF-8 인코딩
        serialized = json.dumps(data).encode('utf-8')
        original_size = len(serialized)

        # ------------------ 압축 처리 구간 (교체 대상) ------------------
        # 현재는 zlib 압축. 이후 ML 모델로 교체 시 아래 한 줄을 바꾸면 됨.
        compressed = zlib.compress(serialized)
        # 예시: compressed = ml_model.encode(serialized)
        # -------------------------------------------------------------

        compressed_size = len(compressed)

        # 압축률 로그 출력
        ratio = (compressed_size / original_size) * 100
        logger.info(f"Original size: {original_size} bytes")
        logger.info(f"Compressed size: {compressed_size} bytes")
        logger.info(f"Compression ratio: {ratio:.2f}%")

        return compressed

if __name__ == "__main__":
    # 디버깅용 샘플 데이터
    sample_data = {
        "name": "ChatGPT",
        "description": "A helpful AI assistant.",
        "features": ["language understanding", "reasoning", "generation"],
        "version": 1.0,
        "active": True
    }

    compressed_result = encoder.compress_data(sample_data)
    print(f"Compressed data: {compressed_result[:50]}... ({len(compressed_result)} bytes)")