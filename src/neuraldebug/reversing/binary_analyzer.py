"""Binary analyzer — the main orchestrator for reverse engineering.

Ties together PE/ELF parsing, string extraction, function finding,
cross-references, and control flow graph construction into a single
``BinaryAnalyzer`` class.
"""

import math
import os
from typing import Dict, List, Optional

from .pe_parser import PEParser
from .elf_parser import ELFParser
from .string_extractor import StringExtractor, ExtractedString
from .func_finder import FunctionFinder, Function
from .xref_engine import XRefEngine
from .cfg_builder import CFGBuilder, CFG
from .x86_decoder import X86Decoder


class BinaryAnalyzer:
    """High-level binary analysis interface.

    Usage::

        analyzer = BinaryAnalyzer("program.exe")
        print(analyzer.info())
        print(analyzer.strings())
        print(analyzer.functions())
        cfg = analyzer.cfg(0x401000)
    """

    def __init__(self, filepath: str):
        self.filepath = os.path.abspath(filepath)
        if not os.path.isfile(self.filepath):
            raise FileNotFoundError(f"File not found: {self.filepath}")

        with open(self.filepath, 'rb') as f:
            self.data = f.read()

        self.file_size = len(self.data)
        self.format = "unknown"  # "PE", "ELF", or "unknown"
        self.bits = 0
        self.arch = "unknown"
        self.image_base = 0

        self._pe: Optional[PEParser] = None
        self._elf: Optional[ELFParser] = None
        self._strings: Optional[List[ExtractedString]] = None
        self._func_finder: Optional[FunctionFinder] = None
        self._xref_engine: Optional[XRefEngine] = None
        self._cfgs: Dict[int, CFG] = {}

        self._detect_format()

    # ---- format detection -------------------------------------------------

    def _detect_format(self):
        if self.data[:2] == b'MZ':
            try:
                self._pe = PEParser(self.data)
                self.format = "PE64" if self._pe.is_pe64 else "PE32"
                self.bits = 64 if self._pe.is_pe64 else 32
                self.arch = self._pe.machine_name
                self.image_base = self._pe.image_base
            except ValueError:
                pass
        elif self.data[:4] == b'\x7fELF':
            try:
                self._elf = ELFParser(self.data)
                self.format = "ELF64" if self._elf.is_64 else "ELF32"
                self.bits = 64 if self._elf.is_64 else 32
                self.arch = self._elf.machine_name
                self.image_base = 0
            except ValueError:
                pass

    # ---- info -------------------------------------------------------------

    def info(self) -> dict:
        """Binary overview."""
        result = {
            "file": os.path.basename(self.filepath),
            "path": self.filepath,
            "size": self.file_size,
            "size_human": _human_size(self.file_size),
            "format": self.format,
            "arch": self.arch,
            "bits": self.bits,
        }
        if self._pe:
            result.update(self._pe.summary())
        elif self._elf:
            result.update(self._elf.summary())
        return result

    # ---- sections ---------------------------------------------------------

    def sections(self) -> List[dict]:
        """List all sections."""
        results = []
        if self._pe:
            for s in self._pe.sections:
                results.append({
                    "name": s.name,
                    "virtual_address": f"0x{s.virtual_address + self.image_base:x}",
                    "virtual_size": f"0x{s.virtual_size:x}",
                    "raw_offset": f"0x{s.raw_offset:x}",
                    "raw_size": f"0x{s.raw_size:x}",
                    "permissions": s.rwx_str,
                    "entropy": round(_entropy(
                        self.data[s.raw_offset:s.raw_offset + s.raw_size]), 2),
                })
        elif self._elf:
            for s in self._elf.sections:
                if not s.name or s.type_id == 0:
                    continue
                results.append({
                    "name": s.name,
                    "type": s.type_name,
                    "address": f"0x{s.address:x}",
                    "offset": f"0x{s.offset:x}",
                    "size": f"0x{s.size:x}",
                    "permissions": s.rwx_str,
                    "entropy": round(_entropy(
                        self.data[s.offset:s.offset + s.size]), 2) if s.size > 0 else 0,
                })
        return results

    # ---- imports / exports ------------------------------------------------

    def imports(self) -> List[dict]:
        """List imported functions grouped by library."""
        results = []
        if self._pe:
            for lib in self._pe.imports:
                funcs = [{"name": f.name, "hint": f.hint,
                          "iat_rva": f"0x{f.iat_rva:x}"} for f in lib.functions]
                results.append({"library": lib.name, "count": len(funcs),
                                "functions": funcs})
        elif self._elf:
            # Group dynamic symbols marked as UND (imported)
            imported = [s for s in self._elf.dyn_symbols
                        if s.section_index == 0 and s.name]
            if imported:
                funcs = [{"name": s.name, "type": s.type_name} for s in imported]
                for lib in self._elf.needed_libs:
                    results.append({"library": lib, "functions": []})
                if results:
                    results[0]["functions"] = funcs
                    results[0]["count"] = len(funcs)
                else:
                    results.append({"library": "(dynamic)",
                                    "count": len(funcs), "functions": funcs})
        return results

    def exports(self) -> List[dict]:
        if self._pe:
            return [{"name": e.name, "ordinal": e.ordinal,
                     "address": f"0x{e.rva + self.image_base:x}"}
                    for e in self._pe.exports]
        elif self._elf:
            exported = [s for s in self._elf.symbols
                        if s.bind == 1 and s.value != 0 and s.name]  # GLOBAL
            return [{"name": s.name, "address": f"0x{s.value:x}",
                     "type": s.type_name, "size": s.size}
                    for s in exported]
        return []

    # ---- strings ----------------------------------------------------------

    def strings(self, min_length: int = 4, limit: int = 500) -> dict:
        """Extract readable strings."""
        if self._strings is None:
            extractor = StringExtractor(self.data, min_length=min_length)
            self._strings = extractor.extract_all()
            secs = self._pe.sections if self._pe else (
                self._elf.sections if self._elf else [])
            StringExtractor.annotate_sections(self._strings, secs)

        strs = self._strings
        summary = StringExtractor(self.data, min_length).summary(strs)
        items = []
        for s in strs[:limit]:
            items.append({
                "offset": f"0x{s.offset:x}",
                "va": f"0x{s.virtual_address:x}" if s.virtual_address else "",
                "section": s.section,
                "encoding": s.encoding,
                "length": s.length,
                "value": s.value[:120],
            })
        summary["strings"] = items
        if len(strs) > limit:
            summary["truncated"] = len(strs) - limit
        return summary

    # ---- functions --------------------------------------------------------

    def functions(self) -> dict:
        """Discover function boundaries."""
        self._ensure_func_finder()
        funcs = self._func_finder.get_sorted_functions()
        summary = self._func_finder.summary()
        summary["functions"] = [
            {
                "address": f"0x{f.address:x}",
                "name": f.name or f"sub_{f.address:x}",
                "size": f.size,
                "source": f.source,
                "num_callers": len(f.called_from),
            }
            for f in funcs[:500]
        ]
        if len(funcs) > 500:
            summary["truncated"] = len(funcs) - 500
        return summary

    # ---- xrefs ------------------------------------------------------------

    def xrefs(self, address: Optional[int] = None) -> dict:
        """Get cross-references. If address given, show refs to/from it."""
        self._ensure_xref_engine()
        if address is not None:
            refs_to = self._xref_engine.get_refs_to(address)
            refs_from = self._xref_engine.get_refs_from(address)
            return {
                "address": f"0x{address:x}",
                "refs_to": [
                    {"from": f"0x{x.from_addr:x}", "type": x.xref_type.value}
                    for x in refs_to
                ],
                "refs_from": [
                    {"to": f"0x{x.to_addr:x}", "type": x.xref_type.value}
                    for x in refs_from
                ],
                "total_to": len(refs_to),
                "total_from": len(refs_from),
            }
        return self._xref_engine.summary()

    # ---- CFG --------------------------------------------------------------

    def cfg(self, address: int, fmt: str = "ascii") -> dict:
        """Build control flow graph for function at address."""
        self._ensure_func_finder()
        func = self._func_finder.functions.get(address)
        if not func:
            # Try to find nearest function
            sorted_addrs = sorted(self._func_finder.functions.keys())
            for a in sorted_addrs:
                f = self._func_finder.functions[a]
                if a <= address < a + (f.size or 0x1000):
                    func = f
                    address = a
                    break
            if not func:
                return {"status": "error",
                        "message": f"No function found at 0x{address:x}"}

        if address not in self._cfgs:
            builder = CFGBuilder(self.bits)
            data_offset = self._va_to_offset(address)
            if data_offset is None:
                return {"status": "error",
                        "message": f"Cannot map 0x{address:x} to file offset"}
            size = func.size or 0x200
            self._cfgs[address] = builder.build(
                self.data, address, size,
                data_offset=data_offset,
                func_name=func.name,
            )

        cfg_obj = self._cfgs[address]
        if fmt == "mermaid":
            return {"format": "mermaid", "graph": CFGBuilder.to_mermaid(cfg_obj)}
        elif fmt == "json":
            return {"format": "json", "graph": CFGBuilder.to_dict(cfg_obj)}
        else:
            return {"format": "ascii", "graph": CFGBuilder.to_ascii(cfg_obj)}

    # ---- disassemble (static) ---------------------------------------------

    def disassemble(self, address: int, count: int = 20) -> dict:
        """Static disassembly at address."""
        offset = self._va_to_offset(address)
        if offset is None:
            return {"status": "error",
                    "message": f"Cannot map 0x{address:x} to file offset"}

        decoder = X86Decoder(self.bits)
        insns = decoder.decode_range(self.data, offset, count, address)
        items = []
        for insn in insns:
            items.append({
                "address": f"0x{insn.address:x}",
                "bytes": insn.hex_bytes,
                "mnemonic": insn.mnemonic or "???",
                "size": insn.size,
                "type": insn.insn_type.name.lower(),
                "target": f"0x{insn.target:x}" if insn.target else None,
            })
        return {
            "address": f"0x{address:x}",
            "count": len(items),
            "instructions": items,
        }

    # ---- entropy ----------------------------------------------------------

    def entropy(self) -> dict:
        """Calculate entropy per section (detect packing/encryption)."""
        sections = self.sections()
        overall = round(_entropy(self.data), 2)
        packed_threshold = 6.8
        suspicious = [s for s in sections
                      if float(s.get("entropy", 0)) >= packed_threshold]
        return {
            "overall_entropy": overall,
            "sections": sections,
            "possibly_packed": len(suspicious) > 0,
            "suspicious_sections": [s["name"] for s in suspicious],
            "threshold": packed_threshold,
        }

    # ---- hexdump ----------------------------------------------------------

    def hexdump(self, offset: int = 0, length: int = 256) -> dict:
        """Raw hex dump at file offset."""
        chunk = self.data[offset:offset + length]
        lines = []
        for i in range(0, len(chunk), 16):
            row = chunk[i:i + 16]
            hex_part = " ".join(f"{b:02x}" for b in row)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            addr = offset + i
            lines.append(f"{addr:08x}  {hex_part:<48}  {ascii_part}")
        return {
            "offset": f"0x{offset:x}",
            "length": len(chunk),
            "hex": "\n".join(lines),
        }

    # ---- headers ----------------------------------------------------------

    def headers(self) -> dict:
        """Detailed header information."""
        if self._pe:
            return {
                "format": self.format,
                "machine": self._pe.machine_name,
                "machine_id": f"0x{self._pe.machine:x}",
                "entry_point": f"0x{self._pe.entry_point_va():x}",
                "entry_point_rva": f"0x{self._pe.entry_point_rva:x}",
                "image_base": f"0x{self._pe.image_base:x}",
                "section_alignment": f"0x{self._pe.section_alignment:x}",
                "file_alignment": f"0x{self._pe.file_alignment:x}",
                "subsystem": self._pe.subsystem_name,
                "dll_characteristics": f"0x{self._pe.dll_characteristics:x}",
                "size_of_image": f"0x{self._pe.size_of_image:x}",
                "size_of_headers": f"0x{self._pe.size_of_headers:x}",
                "checksum": f"0x{self._pe.checksum:x}",
                "timestamp": self._pe.timestamp,
                "data_directories": [
                    {"name": d.name, "rva": f"0x{d.rva:x}",
                     "size": f"0x{d.size:x}"}
                    for d in self._pe.data_dirs if d.rva or d.size
                ],
            }
        elif self._elf:
            from .elf_parser import _ET_NAMES
            return {
                "format": self.format,
                "type": _ET_NAMES.get(self._elf.elf_type, str(self._elf.elf_type)),
                "machine": self._elf.machine_name,
                "entry_point": f"0x{self._elf.entry_point:x}",
                "flags": f"0x{self._elf.flags:x}",
                "sections": len(self._elf.sections),
                "segments": len(self._elf.segments),
                "program_headers": [
                    {"type": seg.type_name, "offset": f"0x{seg.offset:x}",
                     "vaddr": f"0x{seg.vaddr:x}", "filesz": f"0x{seg.filesz:x}",
                     "memsz": f"0x{seg.memsz:x}", "flags": seg.rwx_str}
                    for seg in self._elf.segments
                ],
            }
        return {"format": "unknown"}

    # ---- internal helpers -------------------------------------------------

    def _va_to_offset(self, va: int) -> Optional[int]:
        if self._pe:
            rva = va - self.image_base
            return self._pe.rva_to_offset(rva)
        elif self._elf:
            return self._elf.va_to_offset(va)
        return None

    def _ensure_func_finder(self):
        if self._func_finder is not None:
            return
        self._func_finder = FunctionFinder(
            self.data, self.bits, self.image_base,
            sections=(self._pe.sections if self._pe else
                      self._elf.sections if self._elf else []),
        )
        # Add known symbols
        if self._pe:
            self._func_finder.find_by_exports(self._pe.exports)
            self._func_finder.find_by_entry_point(self._pe.entry_point_va())
            self._func_finder.find_all(self._pe.get_code_sections())
            self._func_finder.estimate_sizes(self._pe.get_code_sections())
        elif self._elf:
            sym_funcs = self._elf.get_functions()
            self._func_finder.find_by_symbols(sym_funcs)
            self._func_finder.find_by_entry_point(self._elf.entry_point)
            self._func_finder.find_all(self._elf.get_code_sections())
            self._func_finder.estimate_sizes(self._elf.get_code_sections())

    def _ensure_xref_engine(self):
        if self._xref_engine is not None:
            return
        self._xref_engine = XRefEngine(self.bits)
        code_secs = (self._pe.get_code_sections() if self._pe else
                     self._elf.get_code_sections() if self._elf else [])
        for sec in code_secs:
            raw_off = getattr(sec, 'raw_offset', getattr(sec, 'offset', 0))
            raw_sz = getattr(sec, 'raw_size', getattr(sec, 'size', 0))
            va = getattr(sec, 'virtual_address', getattr(sec, 'address', 0))
            if hasattr(sec, 'virtual_address'):
                va += self.image_base
            self._xref_engine.analyze_code(self.data, raw_off, raw_sz, va)


# ---------------------------------------------------------------------------
#  Utilities
# ---------------------------------------------------------------------------

def _entropy(data: bytes) -> float:
    """Shannon entropy of a byte sequence (0.0 – 8.0)."""
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    length = len(data)
    ent = 0.0
    for count in freq:
        if count == 0:
            continue
        p = count / length
        ent -= p * math.log2(p)
    return ent


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
