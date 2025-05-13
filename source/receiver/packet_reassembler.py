# packet_reassembler.py (수정)
# -*- coding: utf-8 -*-
"""
패킷 헤더(PKT_ID/SEQ/TOTAL) + payload 재조립기
SEQ는 0-based.
"""
from __future__ import annotations
from typing import Dict, Optional, List, Tuple

class PacketReassemblyError(Exception): pass
class InconsistentPacketError(PacketReassemblyError): pass
class DuplicatePacketError(PacketReassemblyError): pass
class MissingPacketError(PacketReassemblyError): pass
class PacketIdMismatchError(PacketReassemblyError): pass # PKT_ID 불일치 오류 추가

class PacketReassembler:
    def __init__(self) -> None:
        # 이제 PKT_ID 별로 패킷을 저장해야 할 수 있음.
        # 가장 간단한 방법은 Reassembler 인스턴스가 하나의 PKT_ID만 처리하도록 하는 것.
        # 또는 내부에 PKT_ID를 키로 하는 딕셔너리를 두고 여러 PKT_ID를 동시에 처리.
        # 여기서는 하나의 Reassembler가 하나의 메시지(PKT_ID)를 처리한다고 가정하고,
        # reset() 시 PKT_ID도 초기화하도록 함.
        # process_frame 호출 시 PKT_ID를 받아, 현재 처리 중인 PKT_ID와 다르면 오류 발생 또는 리셋.

        self._frames: Dict[int, bytes] = {} # seq -> payload_chunk
        self._current_pkt_id: Optional[int] = None
        self._current_total_frames: Optional[int] = None

    def reset(self) -> None:
        self._frames.clear()
        self._current_pkt_id = None
        self._current_total_frames = None

    def process_frame(self, frame_content: bytes) -> Optional[bytes]:
        """
        프레임 내용 (PKT_ID(1B) | SEQ(1B) | TOTAL(1B) | PAYLOAD_CHUNK)을 처리.
        """
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
            # 빈 메시지는 즉시 None 반환 (또는 특별한 값 반환 후 reset)
            # sender는 빈 메시지를 보내지 않도록 수정했으므로, 이 경우는 거의 발생 안함.
            # 만약 발생한다면, 이 프레임은 무시하고 아무것도 안 하거나, reset.
            # 여기서는 오류로 간주하지 않고, 아직 완성 전으로 취급 (None 반환).
            # 또는, 바로 reset하고 None을 반환할 수도 있음.
            # 이 로직은 sender가 빈 메시지를 보낼 경우 어떻게 처리할지에 따라 달라짐.
            # 현재 sender는 빈 메시지를 보내지 않으므로, total > 0 이어야 함.
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
            # 이전 메시지가 완성되지 않았는데 새 PKT_ID가 들어오면, 이전 것은 유실된 것으로 처리하고 리셋.
            # 또는 PacketIdMismatchError 발생시켜 상위에서 처리하도록 함.
            # 여기서는 이전 것 리셋하고 새 PKT_ID로 시작.
            # raise PacketIdMismatchError(f"PKT_ID 불일치: 현재 {self._current_pkt_id}, 수신 {pkt_id}. 이전 메시지 데이터 유실 가능성.")
            # 더 안전한 방법은, reset()을 외부에서 명시적으로 호출하고, 여기서는 오류 발생시키는 것.
            # 일단은 이전 메시지 데이터 유실시키고 새 PKT_ID로 시작.
            # print(f"경고: PKT_ID 변경됨 ({self._current_pkt_id} -> {pkt_id}). 이전 메시지 미완료 시 데이터 유실.")
            self.reset()
            self._current_pkt_id = pkt_id
            self._current_total_frames = total


        # TOTAL 값이 일관되는지 확인
        if total != self._current_total_frames:
            # raise InconsistentPacketError(f"TOTAL 값 불일치: 현재 {self._current_total_frames}, 수신 {total} (PKT_ID: {pkt_id})")
            # TOTAL이 중간에 바뀌는 것은 심각한 오류. 이전 데이터 폐기.
            # print(f"경고: TOTAL 값 변경됨 (PKT_ID: {pkt_id}, {self._current_total_frames} -> {total}). 데이터 리셋.")
            self.reset()
            self._current_pkt_id = pkt_id # 새 PKT_ID로 간주하고 total도 업데이트
            self._current_total_frames = total


        # 중복 SEQ 검사
        if seq in self._frames:
            # 중복 패킷은 무시하거나 오류 발생. 여기서는 무시.
            # raise DuplicatePacketError(f"중복 패킷: PKT_ID={pkt_id}, SEQ={seq}")
            # print(f"정보: 중복 패킷 수신 (PKT_ID={pkt_id}, SEQ={seq}). 무시함.")
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