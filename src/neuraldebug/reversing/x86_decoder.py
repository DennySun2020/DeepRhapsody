"""Minimal x86/x64 instruction length decoder and control-flow extractor.

This is NOT a full disassembler.  It decodes enough to:
1. Determine instruction boundaries (length).
2. Classify control-flow instructions (CALL, JMP, Jcc, RET, INT).
3. Extract branch/call targets for static analysis.

Handles real-mode, 32-bit, and 64-bit modes including REX/VEX prefixes,
ModR/M, SIB, and displacement/immediate fields.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional


class InsnType(IntEnum):
    OTHER = 0
    CALL = 1
    JMP = 2
    JCC = 3       # conditional jump
    RET = 4
    INT = 5
    NOP = 6
    SYSCALL = 7


@dataclass
class Instruction:
    address: int
    size: int
    raw: bytes
    insn_type: InsnType = InsnType.OTHER
    target: Optional[int] = None   # branch/call target (absolute VA)
    mnemonic: str = ""

    @property
    def is_control_flow(self) -> bool:
        return self.insn_type in (InsnType.CALL, InsnType.JMP,
                                  InsnType.JCC, InsnType.RET)

    @property
    def is_call(self) -> bool:
        return self.insn_type == InsnType.CALL

    @property
    def is_jump(self) -> bool:
        return self.insn_type in (InsnType.JMP, InsnType.JCC)

    @property
    def is_ret(self) -> bool:
        return self.insn_type == InsnType.RET

    @property
    def hex_bytes(self) -> str:
        return self.raw.hex()


# ---------------------------------------------------------------------------
#  Opcode tables (partial — enough for control flow + length decoding)
# ---------------------------------------------------------------------------

# 1-byte opcodes that use ModR/M (need to decode for length)
_HAS_MODRM_1 = set()
for _base in (0x00, 0x01, 0x02, 0x03, 0x08, 0x09, 0x0A, 0x0B,
              0x10, 0x11, 0x12, 0x13, 0x18, 0x19, 0x1A, 0x1B,
              0x20, 0x21, 0x22, 0x23, 0x28, 0x29, 0x2A, 0x2B,
              0x30, 0x31, 0x32, 0x33, 0x38, 0x39, 0x3A, 0x3B,
              0x62, 0x63, 0x69, 0x6B,
              0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
              0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E, 0x8F,
              0xC0, 0xC1, 0xC4, 0xC5, 0xC6, 0xC7,
              0xD0, 0xD1, 0xD2, 0xD3,
              0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF,
              0xF6, 0xF7, 0xFE, 0xFF):
    _HAS_MODRM_1.add(_base)

# 2-byte opcodes (0F xx) that use ModR/M
_HAS_MODRM_0F = set()
for _r in range(0x00, 0x04):
    _HAS_MODRM_0F.add(_r)
for _r in range(0x10, 0x18):
    _HAS_MODRM_0F.add(_r)
for _r in range(0x20, 0x28):
    _HAS_MODRM_0F.add(_r)
for _r in range(0x28, 0x30):
    _HAS_MODRM_0F.add(_r)
for _r in range(0x40, 0x50):
    _HAS_MODRM_0F.add(_r)  # CMOVcc
for _r in range(0x50, 0x70):
    _HAS_MODRM_0F.add(_r)
for _r in range(0x70, 0x77):
    _HAS_MODRM_0F.add(_r)
for _r in range(0x90, 0xA0):
    _HAS_MODRM_0F.add(_r)  # SETcc
for _r in range(0xA3, 0xAC):
    _HAS_MODRM_0F.add(_r)
for _r in range(0xAF, 0xC0):
    _HAS_MODRM_0F.add(_r)
for _r in range(0xC0, 0xD0):
    _HAS_MODRM_0F.add(_r)
for _r in range(0xD0, 0x100):
    _HAS_MODRM_0F.add(_r)

# Prefix bytes
_LEGACY_PREFIXES = {0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67,
                    0xF0, 0xF2, 0xF3}


# ---------------------------------------------------------------------------
#  Decoder
# ---------------------------------------------------------------------------

class X86Decoder:
    """Decode x86/x64 instructions for length and control-flow analysis."""

    def __init__(self, bits: int = 64):
        assert bits in (16, 32, 64)
        self.bits = bits

    def decode_one(self, data: bytes, offset: int, base_va: int) -> Optional[Instruction]:
        """Decode a single instruction at *offset* in *data*.

        Returns an Instruction or None if decoding fails.
        """
        start = offset
        length = len(data)
        if offset >= length:
            return None

        # --- prefixes ---
        has_operand_override = False
        has_addr_override = False
        has_rex = False
        rex_w = False
        pos = offset

        while pos < length and data[pos] in _LEGACY_PREFIXES:
            if data[pos] == 0x66:
                has_operand_override = True
            elif data[pos] == 0x67:
                has_addr_override = True
            pos += 1

        # REX prefix (64-bit mode only)
        if self.bits == 64 and pos < length and 0x40 <= data[pos] <= 0x4F:
            has_rex = True
            rex_w = bool(data[pos] & 0x08)
            pos += 1

        if pos >= length:
            return None

        opcode = data[pos]
        pos += 1
        insn_type = InsnType.OTHER
        target = None
        mnemonic = ""
        is_twobyte = False

        # --- two-byte escape ---
        if opcode == 0x0F:
            if pos >= length:
                return None
            opcode2 = data[pos]
            pos += 1
            is_twobyte = True

            # 0F 80-8F: Jcc rel32/rel16
            if 0x80 <= opcode2 <= 0x8F:
                insn_type = InsnType.JCC
                _JCC_NAMES = ["jo","jno","jb","jnb","jz","jnz","jbe","ja",
                              "js","jns","jp","jnp","jl","jge","jle","jg"]
                mnemonic = _JCC_NAMES[opcode2 - 0x80]
                if has_operand_override:
                    rel = _read_signed(data, pos, 2)
                    pos += 2
                else:
                    rel = _read_signed(data, pos, 4)
                    pos += 4
                va = base_va + (pos - start)
                target = va + rel
            # 0F 05: SYSCALL
            elif opcode2 == 0x05:
                insn_type = InsnType.SYSCALL
                mnemonic = "syscall"
            # 0F 1F: multi-byte NOP
            elif opcode2 == 0x1F:
                insn_type = InsnType.NOP
                mnemonic = "nop"
                if pos < length:
                    pos += _modrm_length(data, pos, self.bits, has_addr_override)
            else:
                # Generic 2-byte with ModR/M
                if opcode2 in _HAS_MODRM_0F:
                    if pos < length:
                        pos += _modrm_length(data, pos, self.bits, has_addr_override)
                    # Some instructions have immediate bytes too
                    if opcode2 in (0x70, 0x71, 0x72, 0x73, 0xC2, 0xC4,
                                   0xC5, 0xC6, 0xA4, 0xAC):
                        pos += 1  # imm8
        else:
            # --- single-byte opcodes ---

            # RET
            if opcode in (0xC3, 0xCB):
                insn_type = InsnType.RET
                mnemonic = "ret"
            elif opcode in (0xC2, 0xCA):
                insn_type = InsnType.RET
                mnemonic = "ret"
                pos += 2  # imm16

            # CALL rel32
            elif opcode == 0xE8:
                insn_type = InsnType.CALL
                mnemonic = "call"
                if has_operand_override:
                    rel = _read_signed(data, pos, 2)
                    pos += 2
                else:
                    rel = _read_signed(data, pos, 4)
                    pos += 4
                va = base_va + (pos - start)
                target = va + rel

            # JMP rel32
            elif opcode == 0xE9:
                insn_type = InsnType.JMP
                mnemonic = "jmp"
                if has_operand_override:
                    rel = _read_signed(data, pos, 2)
                    pos += 2
                else:
                    rel = _read_signed(data, pos, 4)
                    pos += 4
                va = base_va + (pos - start)
                target = va + rel

            # JMP rel8
            elif opcode == 0xEB:
                insn_type = InsnType.JMP
                mnemonic = "jmp"
                rel = _read_signed(data, pos, 1)
                pos += 1
                va = base_va + (pos - start)
                target = va + rel

            # Short Jcc (70-7F)
            elif 0x70 <= opcode <= 0x7F:
                insn_type = InsnType.JCC
                _JCC_NAMES = ["jo","jno","jb","jnb","jz","jnz","jbe","ja",
                              "js","jns","jp","jnp","jl","jge","jle","jg"]
                mnemonic = _JCC_NAMES[opcode - 0x70]
                rel = _read_signed(data, pos, 1)
                pos += 1
                va = base_va + (pos - start)
                target = va + rel

            # LOOP/JCXZ (E0-E3)
            elif 0xE0 <= opcode <= 0xE3:
                insn_type = InsnType.JCC
                mnemonic = ["loopne", "loope", "loop", "jcxz"][opcode - 0xE0]
                rel = _read_signed(data, pos, 1)
                pos += 1
                va = base_va + (pos - start)
                target = va + rel

            # INT
            elif opcode == 0xCC:
                insn_type = InsnType.INT
                mnemonic = "int3"
            elif opcode == 0xCD:
                insn_type = InsnType.INT
                mnemonic = "int"
                pos += 1

            # NOP
            elif opcode == 0x90:
                insn_type = InsnType.NOP
                mnemonic = "nop"

            # --- opcodes with ModR/M ---
            elif opcode in _HAS_MODRM_1:
                if pos < length:
                    modrm_len = _modrm_length(data, pos, self.bits, has_addr_override)
                    modrm = data[pos]
                    pos += modrm_len

                    # FF /2 = CALL r/m, FF /4 = JMP r/m
                    if opcode == 0xFF:
                        reg = (modrm >> 3) & 7
                        if reg == 2:
                            insn_type = InsnType.CALL
                            mnemonic = "call"
                        elif reg == 4:
                            insn_type = InsnType.JMP
                            mnemonic = "jmp"
                        elif reg == 6:
                            mnemonic = "push"

                    # Immediate operands for group opcodes
                    if opcode in (0x80, 0x82):
                        pos += 1  # imm8
                    elif opcode == 0x81:
                        pos += 4 if not has_operand_override else 2  # imm32/16
                    elif opcode == 0x83:
                        pos += 1  # imm8
                    elif opcode == 0x69:
                        pos += 4 if not has_operand_override else 2
                    elif opcode == 0x6B:
                        pos += 1
                    elif opcode in (0xC0, 0xC1):
                        pos += 1  # shift imm8
                    elif opcode == 0xC6:
                        pos += 1  # MOV r/m8, imm8
                    elif opcode == 0xC7:
                        pos += 4 if not has_operand_override else 2
                    elif opcode == 0xF6:
                        reg = (modrm >> 3) & 7
                        if reg in (0, 1):
                            pos += 1  # TEST r/m8, imm8
                    elif opcode == 0xF7:
                        reg = (modrm >> 3) & 7
                        if reg in (0, 1):
                            pos += 4 if not has_operand_override else 2

            # --- immediate-only opcodes ---
            elif opcode in (0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C,
                            0x6A, 0xA8, 0xD4, 0xD5):
                pos += 1  # imm8
            elif opcode in (0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D,
                            0x68, 0xA9):
                pos += 4 if not has_operand_override else 2  # imm32/16
            elif opcode == 0xEA:  # far JMP
                insn_type = InsnType.JMP
                mnemonic = "jmp far"
                pos += 6 if not has_operand_override else 4
            elif opcode == 0x9A:  # far CALL
                insn_type = InsnType.CALL
                mnemonic = "call far"
                pos += 6 if not has_operand_override else 4

            # MOV r8, imm8 (B0-B7)
            elif 0xB0 <= opcode <= 0xB7:
                pos += 1
            # MOV r32/64, imm32/64 (B8-BF)
            elif 0xB8 <= opcode <= 0xBF:
                if self.bits == 64 and rex_w:
                    pos += 8  # imm64
                else:
                    pos += 4 if not has_operand_override else 2

            # MOV AL/AX, moffs (A0-A3)
            elif opcode in (0xA0, 0xA1, 0xA2, 0xA3):
                if self.bits == 64:
                    pos += 8
                elif has_addr_override:
                    pos += 2
                else:
                    pos += 4

            # ENTER
            elif opcode == 0xC8:
                pos += 3  # imm16 + imm8

            # String prefixed ops, single-byte ops (40-5F in 32-bit), etc.
            # Already handled by default (pos stays same = 1-byte insn)

        insn_size = pos - start
        if insn_size <= 0 or insn_size > 15:
            # x86 max instruction length is 15 bytes; bail out
            insn_size = 1
            insn_type = InsnType.OTHER
            target = None

        raw = data[start:start + insn_size]
        return Instruction(
            address=base_va + (start - offset) + (offset - start) + base_va * 0,
            size=insn_size, raw=raw,
            insn_type=insn_type, target=target, mnemonic=mnemonic,
        )

    def decode_range(self, data: bytes, offset: int, count: int,
                     base_va: int) -> List[Instruction]:
        """Decode up to *count* instructions starting at *offset*."""
        instructions = []
        pos = offset
        for _ in range(count):
            insn = self.decode_one(data, pos, base_va + (pos - offset))
            if insn is None:
                break
            insn.address = base_va + (pos - offset)
            instructions.append(insn)
            pos += insn.size
            if pos >= len(data):
                break
        return instructions

    def decode_until_ret(self, data: bytes, offset: int,
                         base_va: int, max_insns: int = 10000) -> List[Instruction]:
        """Decode instructions until a RET (or limit)."""
        instructions = []
        pos = offset
        for _ in range(max_insns):
            insn = self.decode_one(data, pos, base_va + (pos - offset))
            if insn is None:
                break
            insn.address = base_va + (pos - offset)
            instructions.append(insn)
            pos += insn.size
            if insn.is_ret:
                break
            if pos >= len(data):
                break
        return instructions


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _read_signed(data: bytes, offset: int, size: int) -> int:
    if offset + size > len(data):
        return 0
    val = int.from_bytes(data[offset:offset + size], 'little', signed=True)
    return val


def _modrm_length(data: bytes, offset: int, bits: int,
                  addr_override: bool) -> int:
    """Return the total bytes consumed by ModR/M + SIB + displacement."""
    if offset >= len(data):
        return 1

    modrm = data[offset]
    mod = (modrm >> 6) & 3
    rm = modrm & 7
    length = 1  # ModR/M byte itself

    addr_size = bits
    if addr_override:
        addr_size = 32 if bits == 64 else 16 if bits == 32 else 32

    if addr_size == 16:
        # 16-bit addressing
        if mod == 0 and rm == 6:
            length += 2  # disp16
        elif mod == 1:
            length += 1
        elif mod == 2:
            length += 2
    else:
        # 32/64-bit addressing
        if mod == 0:
            if rm == 4:
                length += 1  # SIB
                if offset + 1 < len(data):
                    sib = data[offset + 1]
                    if (sib & 7) == 5:
                        length += 4  # disp32
            elif rm == 5:
                length += 4  # disp32 (or RIP-relative in 64-bit)
        elif mod == 1:
            if rm == 4:
                length += 1  # SIB
            length += 1  # disp8
        elif mod == 2:
            if rm == 4:
                length += 1  # SIB
            length += 4  # disp32
        # mod == 3: register direct, no extra bytes

    return length
