# packet_reassembler.py
"""
JSON 기반 패킷 재조립 클래스 및 예외 정의
패킷 구조: {'seq':int,'total':int,'payload':base64 문자열}
"""
import json, base64, binascii
from typing import Dict, Optional

class PacketReassemblyError(Exception): pass
class PacketFormatError(PacketReassemblyError): pass
class InconsistentPacketDataError(PacketReassemblyError): pass
class DuplicatePacketError(PacketReassemblyError): pass

class PacketReassembler:
    """
    JSON 패킷을 순서대로 저장하고, 전부 수신 시 바이트 메시지 반환
    """
    def __init__(self):
        self._packets: Dict[int, bytes] = {}
        self._expected_total: Optional[int] = None
        print("[Reassembler] 초기화 완료.")

    def _reset_state(self):
        self._packets.clear()
        self._expected_total = None
        print("[Reassembler] 상태 초기화 완료.")

    def process_line(self, line: str) -> Optional[bytes]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise PacketFormatError(f"잘못된 JSON 형식: {e}")
        if not isinstance(data, dict):
            raise PacketFormatError("파싱된 데이터가 딕셔너리가 아님")
        seq = data.get('seq'); total = data.get('total'); b64 = data.get('payload')
        if seq is None or total is None or b64 is None:
            raise PacketFormatError("필수 키 누락(seq,total,payload)")
        if not isinstance(seq,int) or not isinstance(total,int) or not isinstance(b64,str):
            raise PacketFormatError("키 타입 오류(seq:int,total:int,payload:str)")
        if total <= 0 or not (1 <= seq <= total):
            raise PacketFormatError("seq/total 범위 오류")
        try:
            payload = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise PacketFormatError(f"잘못된 Base64: {e}")
        if self._expected_total is None:
            self._expected_total = total
        elif total != self._expected_total:
            self._reset_state()
            raise InconsistentPacketDataError("총 패킷 수 불일치")
        if seq in self._packets:
            raise DuplicatePacketError(f"중복 패킷 seq={seq}")
        self._packets[seq] = payload
        print(f"[Reassembler] 저장: {seq}/{self._expected_total}")
        if len(self._packets) == self._expected_total:
            if set(self._packets.keys()) != set(range(1, self._expected_total+1)):
                self._reset_state()
                raise PacketReassemblyError("누락된 패킷 존재")
            try:
                assembled = b"".join(self._packets[i] for i in sorted(self._packets))
            except Exception as e:
                self._reset_state()
                raise PacketReassemblyError(f"병합 실패: {e}")
            self._reset_state()
            return assembled
        return None

    def reset(self):
        self._reset_state()
