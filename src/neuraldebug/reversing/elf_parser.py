"""ELF (Executable and Linkable Format) parser.

Parses ELF32 and ELF64 binaries using only the Python standard library
(``struct``).  Extracts:

* ELF header (class, machine, entry point, flags)
* Section headers (name, type, address, offset, size, flags)
* Program headers / segments
* Symbol tables (.symtab, .dynsym)
* Dynamic section (needed libraries, PLT/GOT)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------

@dataclass
class ELFSection:
    name: str
    type_id: int
    flags: int
    address: int
    offset: int
    size: int
    link: int = 0
    info: int = 0
    entry_size: int = 0

    @property
    def is_executable(self) -> bool:
        return bool(self.flags & 0x4)  # SHF_EXECINSTR

    @property
    def is_writable(self) -> bool:
        return bool(self.flags & 0x1)  # SHF_WRITE

    @property
    def is_alloc(self) -> bool:
        return bool(self.flags & 0x2)  # SHF_ALLOC

    @property
    def rwx_str(self) -> str:
        r = "R" if self.is_alloc else "-"
        w = "W" if self.is_writable else "-"
        x = "X" if self.is_executable else "-"
        return r + w + x

    @property
    def type_name(self) -> str:
        return _SHT_NAMES.get(self.type_id, f"0x{self.type_id:x}")

    def contains_va(self, va: int) -> bool:
        return self.address <= va < self.address + self.size


@dataclass
class ELFSegment:
    type_id: int
    flags: int
    offset: int
    vaddr: int
    paddr: int
    filesz: int
    memsz: int
    align: int

    @property
    def type_name(self) -> str:
        return _PT_NAMES.get(self.type_id, f"0x{self.type_id:x}")

    @property
    def rwx_str(self) -> str:
        r = "R" if self.flags & 4 else "-"
        w = "W" if self.flags & 2 else "-"
        x = "X" if self.flags & 1 else "-"
        return r + w + x


@dataclass
class ELFSymbol:
    name: str
    value: int
    size: int
    type_id: int
    bind: int
    section_index: int

    @property
    def type_name(self) -> str:
        return _STT_NAMES.get(self.type_id, f"0x{self.type_id:x}")

    @property
    def bind_name(self) -> str:
        return _STB_NAMES.get(self.bind, f"0x{self.bind:x}")

    @property
    def is_function(self) -> bool:
        return self.type_id == 2  # STT_FUNC


@dataclass
class ImportFunction:
    name: str
    ordinal: int = 0
    plt_address: int = 0


@dataclass
class ImportLibrary:
    name: str
    functions: List[ImportFunction] = field(default_factory=list)


@dataclass
class ExportEntry:
    name: str
    ordinal: int
    rva: int


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

_MACHINE_NAMES = {
    0x03: "x86", 0x3E: "x64", 0x28: "ARM", 0xB7: "ARM64",
    0x08: "MIPS", 0x14: "PowerPC", 0x15: "PowerPC64",
    0xF3: "RISC-V", 0x2A: "SuperH",
}

_SHT_NAMES = {
    0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB",
    4: "RELA", 5: "HASH", 6: "DYNAMIC", 7: "NOTE",
    8: "NOBITS", 9: "REL", 10: "SHLIB", 11: "DYNSYM",
    14: "INIT_ARRAY", 15: "FINI_ARRAY",
}

_PT_NAMES = {
    0: "NULL", 1: "LOAD", 2: "DYNAMIC", 3: "INTERP",
    4: "NOTE", 5: "SHLIB", 6: "PHDR", 7: "TLS",
    0x6474e550: "GNU_EH_FRAME", 0x6474e551: "GNU_STACK",
    0x6474e552: "GNU_RELRO", 0x6474e553: "GNU_PROPERTY",
}

_STT_NAMES = {
    0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION",
    4: "FILE", 5: "COMMON", 6: "TLS",
}

_STB_NAMES = {
    0: "LOCAL", 1: "GLOBAL", 2: "WEAK",
}

_ET_NAMES = {
    0: "NONE", 1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE",
}


# ---------------------------------------------------------------------------
#  Parser
# ---------------------------------------------------------------------------

class ELFParser:
    """Parse an ELF binary from raw bytes."""

    def __init__(self, data: bytes):
        self.data = data
        self.sections: List[ELFSection] = []
        self.segments: List[ELFSegment] = []
        self.symbols: List[ELFSymbol] = []
        self.dyn_symbols: List[ELFSymbol] = []
        self.needed_libs: List[str] = []

        self.is_64 = False
        self.little_endian = True
        self.machine = 0
        self.machine_name = "unknown"
        self.elf_type = 0
        self.entry_point = 0
        self.flags = 0

        self._parse()

    # ---- helpers ----------------------------------------------------------

    def _endian(self) -> str:
        return '<' if self.little_endian else '>'

    def _read(self, offset: int, fmt: str) -> tuple:
        size = struct.calcsize(fmt)
        if offset + size > len(self.data):
            raise ValueError(f"Read past end: offset={offset:#x}, size={size}")
        return struct.unpack_from(fmt, self.data, offset)

    def _read_cstring(self, offset: int, max_len: int = 256) -> str:
        end = self.data.find(b'\x00', offset, offset + max_len)
        if end == -1:
            end = offset + max_len
        return self.data[offset:end].decode('ascii', errors='replace')

    def va_to_offset(self, va: int) -> Optional[int]:
        for seg in self.segments:
            if seg.type_id == 1 and seg.vaddr <= va < seg.vaddr + seg.filesz:
                return va - seg.vaddr + seg.offset
        for sec in self.sections:
            if sec.address <= va < sec.address + sec.size:
                return va - sec.address + sec.offset
        return None

    # ---- main parse -------------------------------------------------------

    def _parse(self):
        if len(self.data) < 52:
            raise ValueError("File too small for ELF")
        if self.data[:4] != b'\x7fELF':
            raise ValueError("Not an ELF file (missing magic)")

        ei_class = self.data[4]
        self.is_64 = (ei_class == 2)
        self.little_endian = (self.data[5] == 1)
        e = self._endian()

        if self.is_64:
            (self.elf_type, self.machine, _, self.entry_point,
             ph_off, sh_off, self.flags, eh_size,
             ph_size, ph_num, sh_size, sh_num, sh_strndx,
             ) = self._read(16, f'{e}HHIQQQIHHHHHH')
        else:
            (self.elf_type, self.machine, _, self.entry_point,
             ph_off, sh_off, self.flags, eh_size,
             ph_size, ph_num, sh_size, sh_num, sh_strndx,
             ) = self._read(16, f'{e}HHIIIIIHHHHHH')

        self.machine_name = _MACHINE_NAMES.get(self.machine, f"0x{self.machine:x}")

        self._parse_sections(sh_off, sh_size, sh_num, sh_strndx)
        self._parse_segments(ph_off, ph_size, ph_num)
        self._parse_symbols()
        self._parse_dynamic()

    def _parse_sections(self, sh_off: int, sh_size: int, sh_num: int, sh_strndx: int):
        if sh_off == 0 or sh_num == 0:
            return
        e = self._endian()

        # First pass: read raw section headers
        raw_sections = []
        for i in range(sh_num):
            off = sh_off + i * sh_size
            if off + sh_size > len(self.data):
                break
            if self.is_64:
                (name_idx, sh_type, sh_flags, sh_addr,
                 sh_offset, sh_sz, sh_link, sh_info,
                 sh_align, sh_entsize,
                 ) = self._read(off, f'{e}IIQQQQIIqq')
            else:
                (name_idx, sh_type, sh_flags, sh_addr,
                 sh_offset, sh_sz, sh_link, sh_info,
                 sh_align, sh_entsize,
                 ) = self._read(off, f'{e}IIIIIIIIII')
            raw_sections.append((name_idx, sh_type, sh_flags, sh_addr,
                                 sh_offset, sh_sz, sh_link, sh_info, sh_entsize))

        # Get section name string table
        strtab_data = b''
        if sh_strndx < len(raw_sections):
            _, _, _, _, str_off, str_sz, _, _, _ = raw_sections[sh_strndx]
            if str_off + str_sz <= len(self.data):
                strtab_data = self.data[str_off:str_off + str_sz]

        for (name_idx, sh_type, sh_flags, sh_addr,
             sh_offset, sh_sz, sh_link, sh_info, sh_entsize) in raw_sections:
            name = ""
            if strtab_data and name_idx < len(strtab_data):
                end = strtab_data.find(b'\x00', name_idx)
                if end == -1:
                    end = len(strtab_data)
                name = strtab_data[name_idx:end].decode('ascii', errors='replace')
            self.sections.append(ELFSection(
                name=name, type_id=sh_type, flags=sh_flags,
                address=sh_addr, offset=sh_offset, size=sh_sz,
                link=sh_link, info=sh_info, entry_size=sh_entsize,
            ))

    def _parse_segments(self, ph_off: int, ph_size: int, ph_num: int):
        if ph_off == 0 or ph_num == 0:
            return
        e = self._endian()
        for i in range(ph_num):
            off = ph_off + i * ph_size
            if off + ph_size > len(self.data):
                break
            if self.is_64:
                (p_type, p_flags, p_offset, p_vaddr, p_paddr,
                 p_filesz, p_memsz, p_align,
                 ) = self._read(off, f'{e}IIQQQQQQ')
            else:
                (p_type, p_offset, p_vaddr, p_paddr,
                 p_filesz, p_memsz, p_flags, p_align,
                 ) = self._read(off, f'{e}IIIIIIII')
            self.segments.append(ELFSegment(
                type_id=p_type, flags=p_flags, offset=p_offset,
                vaddr=p_vaddr, paddr=p_paddr, filesz=p_filesz,
                memsz=p_memsz, align=p_align,
            ))

    def _parse_symbols(self):
        for sec in self.sections:
            if sec.type_id not in (2, 11):  # SHT_SYMTAB or SHT_DYNSYM
                continue
            strtab_sec = self.sections[sec.link] if sec.link < len(self.sections) else None
            strtab = b''
            if strtab_sec:
                strtab = self.data[strtab_sec.offset:strtab_sec.offset + strtab_sec.size]

            entry_size = sec.entry_size or (24 if self.is_64 else 16)
            count = sec.size // entry_size if entry_size else 0
            e = self._endian()
            target = self.dyn_symbols if sec.type_id == 11 else self.symbols

            for i in range(count):
                off = sec.offset + i * entry_size
                if off + entry_size > len(self.data):
                    break
                if self.is_64:
                    (name_idx, st_info, _, st_shndx,
                     st_value, st_size,
                     ) = self._read(off, f'{e}IBBHQQ')
                else:
                    (name_idx, st_value, st_size,
                     st_info, _, st_shndx,
                     ) = self._read(off, f'{e}IIIBBH')

                name = ""
                if strtab and name_idx < len(strtab):
                    end = strtab.find(b'\x00', name_idx)
                    if end == -1:
                        end = len(strtab)
                    name = strtab[name_idx:end].decode('ascii', errors='replace')

                sym_type = st_info & 0xF
                sym_bind = st_info >> 4
                target.append(ELFSymbol(
                    name=name, value=st_value, size=st_size,
                    type_id=sym_type, bind=sym_bind,
                    section_index=st_shndx,
                ))

    def _parse_dynamic(self):
        dyn_sec = next((s for s in self.sections if s.type_id == 6), None)
        if not dyn_sec:
            return
        e = self._endian()
        entry_size = 16 if self.is_64 else 8
        count = dyn_sec.size // entry_size
        strtab_addr = 0

        # First pass: find DT_STRTAB
        entries = []
        for i in range(count):
            off = dyn_sec.offset + i * entry_size
            if off + entry_size > len(self.data):
                break
            if self.is_64:
                tag, val = self._read(off, f'{e}qQ')
            else:
                tag, val = self._read(off, f'{e}iI')
            entries.append((tag, val))
            if tag == 5:  # DT_STRTAB
                strtab_addr = val
            if tag == 0:  # DT_NULL
                break

        strtab_off = self.va_to_offset(strtab_addr) if strtab_addr else None

        for tag, val in entries:
            if tag == 1 and strtab_off:  # DT_NEEDED
                name = self._read_cstring(strtab_off + val)
                self.needed_libs.append(name)

    # ---- public helpers ---------------------------------------------------

    def get_code_sections(self) -> List[ELFSection]:
        return [s for s in self.sections if s.is_executable and s.is_alloc]

    def get_section_data(self, section: ELFSection) -> bytes:
        return self.data[section.offset:section.offset + section.size]

    def get_functions(self) -> List[ELFSymbol]:
        all_syms = self.symbols + self.dyn_symbols
        return [s for s in all_syms if s.is_function and s.value != 0]

    def summary(self) -> dict:
        elf_type_name = _ET_NAMES.get(self.elf_type, str(self.elf_type))
        funcs = self.get_functions()
        return {
            "format": "ELF64" if self.is_64 else "ELF32",
            "type": elf_type_name,
            "machine": self.machine_name,
            "entry_point": f"0x{self.entry_point:x}",
            "sections": len(self.sections),
            "segments": len(self.segments),
            "symbols": len(self.symbols),
            "dynamic_symbols": len(self.dyn_symbols),
            "functions": len(funcs),
            "needed_libraries": self.needed_libs,
        }
