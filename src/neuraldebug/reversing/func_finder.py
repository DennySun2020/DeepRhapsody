"""Function boundary detector for stripped binaries.

Combines multiple heuristics to locate function start addresses:

1. **Symbol-based** — from PE exports, ELF .symtab/.dynsym
2. **Entry point** — the binary's declared entry point
3. **Prologue patterns** — byte sequences like ``push ebp; mov ebp,esp``
   or ``push rbp; mov rbp,rsp``; also ``sub rsp, imm``
4. **Call-target analysis** — decode CALL instructions and collect
   their direct targets (each target is a likely function start)
5. **Exception/unwind data** — PE .pdata section (future)
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .x86_decoder import X86Decoder, InsnType


@dataclass
class Function:
    address: int
    size: int = 0
    name: str = ""
    source: str = ""          # how it was discovered
    calls_to: List[int] = field(default_factory=list)
    called_from: List[int] = field(default_factory=list)
    num_instructions: int = 0
    num_basic_blocks: int = 0


# x86 / x64 prologue patterns (raw bytes)
_PROLOGUE_PATTERNS_32 = [
    # push ebp; mov ebp, esp
    re.compile(rb'\x55\x89\xe5'),
    re.compile(rb'\x55\x8b\xec'),
    # push ebp; sub esp, ...
    re.compile(rb'\x55\x83\xec'),
    # push edi; push esi; push ebx (common gcc prologue)
    re.compile(rb'\x57\x56\x53'),
    # sub esp, imm32
    re.compile(rb'\x81\xec'),
]

_PROLOGUE_PATTERNS_64 = [
    # push rbp; mov rbp, rsp
    re.compile(rb'\x55\x48\x89\xe5'),
    re.compile(rb'\x55\x48\x8b\xec'),
    # sub rsp, imm8
    re.compile(rb'\x48\x83\xec'),
    # sub rsp, imm32
    re.compile(rb'\x48\x81\xec'),
    # push rbp (common start even without frame pointer)
    re.compile(rb'\x55\x41'),
    # push r12-r15 (callee-saved, common start)
    re.compile(rb'\x41\x54'),
    re.compile(rb'\x41\x55'),
    re.compile(rb'\x41\x56'),
    re.compile(rb'\x41\x57'),
]

# Patterns that indicate function boundaries (separators)
_PADDING_PATTERNS = [
    rb'\xcc\xcc\xcc\xcc',        # int3 padding (MSVC)
    rb'\x90\x90\x90\x90',        # nop padding (GCC)
    rb'\x0f\x1f\x44\x00\x00',    # multi-byte NOP
]


class FunctionFinder:
    """Discover function boundaries in a binary."""

    def __init__(self, data: bytes, bits: int = 64,
                 image_base: int = 0, sections=None):
        self.data = data
        self.bits = bits
        self.image_base = image_base
        self.sections = sections or []
        self.decoder = X86Decoder(bits)
        self.functions: Dict[int, Function] = {}

    def add_known_function(self, address: int, name: str = "",
                           source: str = "symbol"):
        if address not in self.functions:
            self.functions[address] = Function(
                address=address, name=name, source=source,
            )
        else:
            if name and not self.functions[address].name:
                self.functions[address].name = name

    def find_by_symbols(self, symbols: list):
        """Add functions from symbol table entries."""
        for sym in symbols:
            addr = getattr(sym, 'rva', 0) or getattr(sym, 'value', 0)
            if hasattr(sym, 'rva'):
                addr += self.image_base
            name = getattr(sym, 'name', '')
            if addr and name:
                self.add_known_function(addr, name, "symbol")

    def find_by_exports(self, exports: list):
        """Add functions from export table."""
        for exp in exports:
            addr = exp.rva + self.image_base
            self.add_known_function(addr, exp.name, "export")

    def find_by_entry_point(self, entry_va: int):
        """Add the entry point as a function."""
        self.add_known_function(entry_va, "_entry", "entry_point")

    def find_by_prologue(self, section_data: bytes, section_va: int):
        """Scan for function prologue patterns in a code section."""
        patterns = _PROLOGUE_PATTERNS_64 if self.bits == 64 else _PROLOGUE_PATTERNS_32

        for pat in patterns:
            for m in pat.finditer(section_data):
                va = section_va + m.start()
                # Verify it's at a valid alignment or after padding
                if self._is_likely_func_start(section_data, m.start()):
                    if va not in self.functions:
                        self.functions[va] = Function(
                            address=va, source="prologue",
                        )

    def find_by_call_targets(self, section_data: bytes, section_va: int):
        """Decode all CALL instructions and record their targets."""
        insns = self.decoder.decode_range(
            section_data, 0, len(section_data),
            base_va=section_va,
        )
        for insn in insns:
            if insn.is_call and insn.target is not None:
                target = insn.target
                if target not in self.functions:
                    self.functions[target] = Function(
                        address=target, source="call_target",
                    )
                self.functions[target].called_from.append(insn.address)

    def find_all(self, code_sections=None):
        """Run all heuristics on the given code sections."""
        if code_sections is None:
            code_sections = [s for s in self.sections if getattr(s, 'is_executable', False)]

        for sec in code_sections:
            raw_off = getattr(sec, 'raw_offset', getattr(sec, 'offset', 0))
            raw_sz = getattr(sec, 'raw_size', getattr(sec, 'size', 0))
            va = getattr(sec, 'virtual_address', getattr(sec, 'address', 0))
            if hasattr(sec, 'virtual_address'):
                va += self.image_base
            sec_data = self.data[raw_off:raw_off + raw_sz]
            if not sec_data:
                continue
            self.find_by_prologue(sec_data, va)
            self.find_by_call_targets(sec_data, va)

    def estimate_sizes(self, code_sections=None):
        """Estimate function sizes based on address gaps."""
        if not self.functions:
            return
        sorted_addrs = sorted(self.functions.keys())

        # Build section VA ranges for bounds checking
        sec_ranges = []
        if code_sections:
            for sec in code_sections:
                va = getattr(sec, 'virtual_address', getattr(sec, 'address', 0))
                if hasattr(sec, 'virtual_address'):
                    va += self.image_base
                sz = getattr(sec, 'virtual_size', getattr(sec, 'size', 0))
                sec_ranges.append((va, va + sz))

        for i, addr in enumerate(sorted_addrs):
            if i + 1 < len(sorted_addrs):
                next_addr = sorted_addrs[i + 1]
                self.functions[addr].size = next_addr - addr
            else:
                # Last function — estimate from section end
                for (sec_start, sec_end) in sec_ranges:
                    if sec_start <= addr < sec_end:
                        self.functions[addr].size = sec_end - addr
                        break
                else:
                    self.functions[addr].size = 0

    def get_sorted_functions(self) -> List[Function]:
        return sorted(self.functions.values(), key=lambda f: f.address)

    def _is_likely_func_start(self, section_data: bytes, offset: int) -> bool:
        """Heuristic: is this offset likely a real function start?"""
        if offset == 0:
            return True
        # Check if preceded by padding (int3, nop)
        if offset >= 1:
            prev = section_data[offset - 1]
            if prev in (0xCC, 0xC3, 0x90):
                return True
        # Check alignment (functions often aligned to 16 bytes)
        if offset % 16 == 0:
            return True
        if offset % 4 == 0:
            return True
        return False

    def summary(self) -> dict:
        funcs = self.get_sorted_functions()
        by_source = {}
        for f in funcs:
            by_source[f.source] = by_source.get(f.source, 0) + 1
        named = sum(1 for f in funcs if f.name)
        return {
            "total_functions": len(funcs),
            "named": named,
            "unnamed": len(funcs) - named,
            "by_source": by_source,
        }
