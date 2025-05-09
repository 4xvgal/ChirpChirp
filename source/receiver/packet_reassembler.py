# -*- coding: utf-8 -*-
"""
패킷 2 바이트 헤더(seq / total) + payload(≤56 B) 재조립기
"""
from __future__ import annotations
from typing import Dict, Optional, List

class PacketReassemblyError(Exception):      pass
class InconsistentPacketError(PacketReassemblyError):  pass
class DuplicatePacketError(PacketReassemblyError):     pass
class MissingPacketError(PacketReassemblyError):       pass

class PacketReassembler:
    def __init__(self) -> None:
        self._pkt: Dict[int, bytes] = {}
        self._total: Optional[int]  = None

    def reset(self) -> None:
        self._pkt.clear()
        self._total = None

    # ────────── 프레임 처리 ──────────
    def process_frame(self, frame: bytes) -> Optional[bytes]:
        if len(frame) < 3:                       # 2B 헤더 + ≥1B payload
            raise PacketReassemblyError("프레임 길이 오류")

        seq, total = frame[0], frame[1]
        payload    = frame[2:]

        if total == 0 or not (1 <= seq <= total):
            raise PacketReassemblyError("헤더 값 오류")

        # 첫 패킷
        if self._total is None:
            self._total = total
            self._pkt.clear()
        # 총 패킷 수 불일치
        elif total != self._total:
            self.reset()
            raise InconsistentPacketError("total 값이 바뀌었습니다")

        # 중복 검사
        if seq in self._pkt:
            raise DuplicatePacketError(f"중복 패킷 {seq}")

        self._pkt[seq] = payload

        # 모두 모였는가?
        if len(self._pkt) == self._total:
            if set(self._pkt) != set(range(1, self._total + 1)):
                self.reset()
                raise MissingPacketError("패킷 누락")
            data = b"".join(self._pkt[i] for i in range(1, self._total + 1))
            self.reset()
            return data
        return None
