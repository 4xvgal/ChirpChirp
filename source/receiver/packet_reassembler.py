"""
# packet_reassembler.py (수정)
# -*- coding: utf-8 -*-

패킷 헤더(PKT_ID/SEQ/TOTAL) + payload 재조립기
SEQ는 0-based.

from __future__ import annotations
from typing import Dict, Optional, List, Tuple

class PacketReassemblyError(Exception): pass
class InconsistentPacketError(PacketReassemblyError): pass
class DuplicatePacketError(PacketReassemblyError): pass
class MissingPacketError(PacketReassemblyError): pass
class PacketIdMismatchError(PacketReassemblyError): pass # PKT_ID 불일치 오류 추가

class PacketReassembler:
    def __init__(self) -> None:

        self._frames: Dict[int, bytes] = {} # seq -> payload_chunk
        self._current_pkt_id: Optional[int] = None
        self._current_total_frames: Optional[int] = None

    def reset(self) -> None:
        self._frames.clear()
        self._current_pkt_id = None
        self._current_total_frames = None

    def process_frame(self, frame_content: bytes) -> Optional[bytes]:
        
        프레임 내용 (PKT_ID(1B) | SEQ(1B) | TOTAL(1B) | PAYLOAD_CHUNK)을 처리.
        
        header_size = 3 # PKT_ID, SEQ, TOTAL
        if len(frame_content) < header_size: # 최소 헤더 크기 검사 (페이로드는 0일 수 있음)
            raise PacketReassemblyError(f"프레임 길이가 너무 짧습니다 ({len(frame_content)}B). 최소 {header_size}B 필요.")

        pkt_id = frame_content[0]
        seq = frame_content[1]    # 0-based
        total = frame_content[2]  # 전체 프레임 수
        payload_chunk = frame_content[header_size:]

        # total이 0이면 (빈 메시지를 나타내는 특별한 경우), payload도 비어있어야 함
        if total == 0:
            if payload_chunk: # total이 0인데 페이로드가 있으면 오류
                 raise PacketReassemblyError("헤더 값 오류: total=0 이지만 페이로드가 존재합니다.")
            if self._current_total_frames is None and not self._frames: # 첫 프레임인데 total이 0이면
                self.reset() # 안전하게 리셋
            return None


        if not (0 <= seq < total): # SEQ는 0부터 total-1 까지
            raise PacketReassemblyError(f"헤더 값 오류: 유효하지 않은 SEQ/TOTAL (SEQ={seq}, TOTAL={total})")

        # 첫 프레임 수신 시 PKT_ID와 TOTAL 설정
        if self._current_pkt_id is None:
            self._current_pkt_id = pkt_id
            self._current_total_frames = total
            self._frames.clear() # 새 메시지 시작이므로 이전 프레임 정보 삭제
        # PKT_ID가 현재 처리 중인 것과 다른 경우
        elif pkt_id != self._current_pkt_id:
            self.reset()
            self._current_pkt_id = pkt_id
            self._current_total_frames = total


        # TOTAL 값이 일관되는지 확인
        if total != self._current_total_frames:
            self.reset()
            self._current_pkt_id = pkt_id # 새 PKT_ID로 간주하고 total도 업데이트
            self._current_total_frames = total


        # 중복 SEQ 검사
        if seq in self._frames:
            return None # 아무것도 안 함

        self._frames[seq] = payload_chunk

        # 모든 프레임이 모였는지 확인
        if len(self._frames) == self._current_total_frames:
            # 모든 SEQ 번호 (0부터 total-1까지)가 다 있는지 확인
            expected_seqs = set(range(self._current_total_frames))
            if set(self._frames.keys()) != expected_seqs:
                # 이 경우는 로직상 발생하기 어려움 (len이 같으면 모든 seq가 있어야 함)
                # 하지만 방어적으로 추가
                # print(f"오류: 프레임 누락 발생 (PKT_ID: {pkt_id}). 필요한 SEQ: {expected_seqs}, 현재 SEQ: {set(self._frames.keys())}")
                self.reset() # 데이터 불일치로 리셋
                raise MissingPacketError(f"패킷 누락 또는 불일치 (PKT_ID: {pkt_id})")

            # 올바른 순서로 payload_chunk들을 합침
            try:
                full_blob = b"".join(self._frames[s] for s in range(self._current_total_frames))
            except KeyError as e: # 혹시 모를 누락된 seq 접근 시
                self.reset()
                raise MissingPacketError(f"데이터 병합 중 누락된 SEQ 접근: {e} (PKT_ID: {pkt_id})")
            
            # 성공적으로 재조립 후 리셋
            self.reset()
            return full_blob
        
        return None # 아직 모든 프레임이 모이지 않음
"""