import zlib
import json
import base64
import os,sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from receiver.packet_reassembler import PacketReassembler
from transmitter.packetizer import split_into_packets


def packet_to_json(packet: dict) -> str:
    """bytes payload를 base64 인코딩 후 JSON 직렬화"""
    return json.dumps({
        "seq": packet["seq"],
        "total": packet["total"],
        "payload": base64.b64encode(packet["payload"]).decode('utf-8')
    })


def test_packet_reassembler_full_flow():
    # 1. 임의의 원본 데이터 생성 및 압축
    original_data = b"Hello ChirpChirp Reassembler! " * 10

    compressed = zlib.compress(original_data)

    # 2. 패킷으로 분할
    packets = split_into_packets(compressed, max_size=50)

    # 3. JSON(Base64) 문자열로 변환
    json_lines = [packet_to_json(p) for p in packets]
    print(json_lines)
    # 4. PacketReassembler로 하나씩 처리
    reassembler = PacketReassembler()
    result = None
    for line in json_lines:
        maybe = reassembler.process_line(line)
        if maybe:
            result = maybe
            break

    # 5. 완성된 데이터가 존재해야 함
    assert result is not None, "모든 패킷을 처리해도 데이터가 복원되지 않음"

    # 6. 압축 해제 및 원본과 비교
    decompressed = zlib.decompress(result)
    assert decompressed == original_data, "복원된 데이터가 원본과 일치하지 않음"


# ▶️ pytest 없이 직접 실행하고 싶은 경우
if __name__ == "__main__":
    try:
        test_packet_reassembler_full_flow()
        print("테스트 성공: 복원된 데이터가 원본과 일치합니다.")
    except AssertionError as e:
        print(f"테스트 실패: {e}")
    except Exception as ex:
        print(f"예외 발생: {type(ex).__name__}: {ex}")