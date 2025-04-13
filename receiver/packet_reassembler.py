import json
import base64
import binascii
from typing import Dict, Optional

#예외임요요
class PacketReassemblyError(Exception):
    pass

class PacketFormatError(PacketReassemblyError):
    pass

class InconsistentPacketDataError(PacketReassemblyError):
    pass

class DuplicatePacketError(PacketReassemblyError):
    pass

  # _packets: 수신된 페이로드를 순번(seq)을 키로 하여 저장하는 딕셔너리
  # _expected_total: 현재 메시지를 구성하는 총 패킷 수

  # str(json) -> json.loads() -> dict -> base64.b64decode() -> bytes -> decoder.py 

class PacketReassembler:

    def __init__(self):

        self._packets: Dict[int, bytes] = {}
        self._expected_total: Optional[int] = None
        print("[Reassembler] 초기화 완료.")

    def _reset_state(self):  # 내부 상태를 초기화 

        print(f"[Reassembler] 상태 초기화 (예상 패킷 수: {self._expected_total})이다.")
        self._packets.clear()
        self._expected_total = None

    def process_line(self, line: str) -> Optional[bytes]:
        """
        입력된 한 줄의 JSON 문자열(패킷 데이터)을 처리하여 재조립을 시도한다.
        모든 패킷이 수신되면 완성된 메시지를 bytes 형태로 반환하며, 그렇지 않으면 None을 반환한다.
        """
        # 1. JSON 파싱
        try:
            data = json.loads(line)

        except json.JSONDecodeError as e:
            raise PacketFormatError(f"잘못된 JSON 형식이다: {e}") from e

        # 2. 파싱된 데이터가 딕셔너리인지 확인
        if not isinstance(data, dict):
            raise PacketFormatError("파싱된 데이터가 딕셔너리가 아니다.")

        # 3. 필수 필드('seq', 'total', 'payload')를 추출하고 존재, 타입, 값 검증
        seq = data.get('seq')
        total = data.get('total')

        payload_b64 = data.get('payload') # 얘가 몸통통

        if seq is None or total is None or payload_b64 is None:
            raise PacketFormatError("필수 키가 누락되었다.")
        if not isinstance(seq, int) or not isinstance(total, int) or not isinstance(payload_b64, str):
            raise PacketFormatError("키의 데이터 타입 오류이다.")
        if total <= 0 or not (0 < seq <= total):
            raise PacketFormatError("필드 값에 오류가 있다.")

        # 4. Base64 인코딩된 페이로드를 디코딩
        try:
            payload = base64.b64decode(payload_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise PacketFormatError(f"잘못된 Base64 페이로드이다: {e}") from e

        # 5. 첫 패킷 수신 시 또는 이후 패킷의 일관성을 확인인
        if self._expected_total is None:
            # 첫 번째 패킷이라면 총 패킷 수를 설정하고 내부 저장소를 초기
            self._expected_total = total
            self._packets.clear()

        elif total != self._expected_total:
            # 총 패킷 수가 불일치하면 내부 상태를 초기화한 후 예외를 발생
            self._reset_state()
            raise InconsistentPacketDataError("총 패킷 수가 불일치한다.")
        
        # 6. 중복된 패킷의 수신 여부를 확인
        if seq in self._packets:
            raise DuplicatePacketError(f"중복된 패킷 {seq}이(가) 수신되었다.")

        # 7. 검증된 패킷 데이터를 내부 저장소에 저장
        self._packets[seq] = payload
        print(f"[Reassembler] 패킷 저장: {seq}/{self._expected_total} (현재 수신된 패킷: {len(self._packets)}개)이다.")

        # 8. 모든 패킷이 수신되었는지 확인한 후 재조립을 시도
        if len(self._packets) == self._expected_total:

            # 모든 순번이 제대로 수신되었는지 추가 확인한다.
            if set(self._packets.keys()) != set(range(1, self._expected_total + 1)):
                self._reset_state()
                raise PacketReassemblyError("누락된 패킷이 존재한다.")
        
            # 9. 순번에 따라 정렬된 페이로드들을 연결하여 완전한 메시지를 생성한다.
            try:
                reassembled = b"".join(self._packets[k] for k in sorted(self._packets))
            except Exception as e:
                self._reset_state()
                raise PacketReassemblyError(f"병합에 실패하였다: {e}") from e

            # 10. 메시지 재조립 완료 후 내부 상태를 초기화하고 완성된 메시지를 반환
            self._reset_state()
            return reassembled

        # 11. 모든 패킷이 수신되지 않은 경우 None을 반환
        return None

    def get_status(self) -> str:
        # 현재 재조립 상태를 문자열로 반환
        if self._expected_total is None:
            return "대기 중이다: 메시지의 첫 패킷을 기다린다."
        return f"재조립 중이다: {len(self._packets)}/{self._expected_total} 패킷이 수신되었다."

    def reset(self):
        # 외부에서 호출 가능한 상태 리셋 메서두두
        self._reset_state()
        print("[Reassembler] 초기화 완료이다.")
