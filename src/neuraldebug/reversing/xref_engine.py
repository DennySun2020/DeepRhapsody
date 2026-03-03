"""Cross-reference (xref) engine for binary analysis.

Builds a database of references between code and data:

* **Code → Code** — CALL and JMP targets (direct only)
* **Code → Data** — string references, global variable accesses
* **Data → Code** — function pointers in vtables, callbacks
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from .x86_decoder import X86Decoder, InsnType


class XRefType(Enum):
    CALL = "call"
    JUMP = "jump"
    DATA_READ = "data_read"
    DATA_WRITE = "data_write"
    STRING_REF = "string_ref"
    FUNC_PTR = "func_ptr"


@dataclass
class XRef:
    from_addr: int
    to_addr: int
    xref_type: XRefType
    insn_size: int = 0


class XRefEngine:
    """Build and query cross-reference maps."""

    def __init__(self, bits: int = 64):
        self.bits = bits
        self.decoder = X86Decoder(bits)
        # to_addr → list of XRefs pointing TO that address
        self.refs_to: Dict[int, List[XRef]] = {}
        # from_addr → list of XRefs FROM that address
        self.refs_from: Dict[int, List[XRef]] = {}
        self._all_refs: List[XRef] = []

    def _add_ref(self, xref: XRef):
        self._all_refs.append(xref)
        self.refs_to.setdefault(xref.to_addr, []).append(xref)
        self.refs_from.setdefault(xref.from_addr, []).append(xref)

    def analyze_code(self, data: bytes, offset: int, size: int,
                     base_va: int):
        """Scan a code region and extract CALL/JMP xrefs."""
        section_data = data[offset:offset + size]
        insns = self.decoder.decode_range(section_data, 0, size, base_va)

        for insn in insns:
            if insn.target is None:
                continue
            if insn.insn_type == InsnType.CALL:
                self._add_ref(XRef(
                    from_addr=insn.address, to_addr=insn.target,
                    xref_type=XRefType.CALL, insn_size=insn.size,
                ))
            elif insn.insn_type in (InsnType.JMP, InsnType.JCC):
                self._add_ref(XRef(
                    from_addr=insn.address, to_addr=insn.target,
                    xref_type=XRefType.JUMP, insn_size=insn.size,
                ))

    def analyze_string_refs(self, strings: list, code_data: bytes,
                            code_offset: int, code_va: int):
        """Find code instructions that reference string addresses.

        Uses a simple heuristic: scan for 4-byte (32-bit) or 8-byte (64-bit)
        address literals in code that match known string VAs.
        """
        string_addrs = set()
        for s in strings:
            if s.virtual_address:
                string_addrs.add(s.virtual_address)

        if not string_addrs:
            return

        section_data = code_data[code_offset:code_offset + len(code_data)]

        # Search for PUSH imm32 / LEA / MOV with string addresses
        addr_size = 8 if self.bits == 64 else 4
        fmt = 'little'

        for addr in string_addrs:
            addr_bytes = addr.to_bytes(addr_size, fmt)
            pos = 0
            while True:
                idx = section_data.find(addr_bytes, pos)
                if idx == -1:
                    break
                ref_va = code_va + idx
                self._add_ref(XRef(
                    from_addr=ref_va, to_addr=addr,
                    xref_type=XRefType.STRING_REF,
                ))
                pos = idx + 1

    def analyze_func_pointers(self, data: bytes, data_offset: int,
                              data_va: int, data_size: int,
                              func_addrs: Set[int]):
        """Scan data sections for pointers to known functions."""
        if not func_addrs:
            return
        ptr_size = 8 if self.bits == 64 else 4
        section = data[data_offset:data_offset + data_size]

        for i in range(0, len(section) - ptr_size + 1, ptr_size):
            val = int.from_bytes(section[i:i + ptr_size], 'little')
            if val in func_addrs:
                self._add_ref(XRef(
                    from_addr=data_va + i, to_addr=val,
                    xref_type=XRefType.FUNC_PTR,
                ))

    # ---- query API --------------------------------------------------------

    def get_refs_to(self, addr: int) -> List[XRef]:
        return self.refs_to.get(addr, [])

    def get_refs_from(self, addr: int) -> List[XRef]:
        return self.refs_from.get(addr, [])

    def get_callers(self, addr: int) -> List[int]:
        return [x.from_addr for x in self.refs_to.get(addr, [])
                if x.xref_type == XRefType.CALL]

    def get_callees(self, addr: int, func_size: int = 0) -> List[int]:
        """Get all call targets originating from a function."""
        targets = set()
        for ref_addr, refs in self.refs_from.items():
            if func_size == 0 or addr <= ref_addr < addr + func_size:
                for xref in refs:
                    if xref.xref_type == XRefType.CALL:
                        targets.add(xref.to_addr)
        return sorted(targets)

    def get_string_refs_to(self, string_va: int) -> List[int]:
        return [x.from_addr for x in self.refs_to.get(string_va, [])
                if x.xref_type == XRefType.STRING_REF]

    def summary(self) -> dict:
        by_type = {}
        for xref in self._all_refs:
            key = xref.xref_type.value
            by_type[key] = by_type.get(key, 0) + 1
        return {
            "total_xrefs": len(self._all_refs),
            "unique_targets": len(self.refs_to),
            "unique_sources": len(self.refs_from),
            "by_type": by_type,
        }
