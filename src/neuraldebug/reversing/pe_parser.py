"""PE (Portable Executable) format parser.

Parses PE32 and PE64 (PE32+) binaries — .exe, .dll, .sys — using only
the Python standard library (``struct``).  Extracts:

* DOS + PE headers, Optional header (ImageBase, EntryPoint, Subsystem)
* Section table (name, VA, raw offset/size, RWX characteristics)
* Import Directory (libraries + function names / ordinals)
* Export Directory (function names + RVAs)
* Data Directories summary
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------

@dataclass
class PESection:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    characteristics: int

    @property
    def is_executable(self) -> bool:
        return bool(self.characteristics & 0x20000000)

    @property
    def is_writable(self) -> bool:
        return bool(self.characteristics & 0x80000000)

    @property
    def is_readable(self) -> bool:
        return bool(self.characteristics & 0x40000000)

    @property
    def rwx_str(self) -> str:
        r = "R" if self.is_readable else "-"
        w = "W" if self.is_writable else "-"
        x = "X" if self.is_executable else "-"
        return r + w + x

    def contains_rva(self, rva: int) -> bool:
        return self.virtual_address <= rva < self.virtual_address + self.virtual_size


@dataclass
class ImportFunction:
    name: str
    ordinal: int
    hint: int = 0
    iat_rva: int = 0


@dataclass
class ImportLibrary:
    name: str
    functions: List[ImportFunction] = field(default_factory=list)


@dataclass
class ExportEntry:
    name: str
    ordinal: int
    rva: int


@dataclass
class DataDirectory:
    name: str
    rva: int
    size: int


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

_DATA_DIR_NAMES = [
    "Export", "Import", "Resource", "Exception",
    "Security", "BaseReloc", "Debug", "Architecture",
    "GlobalPtr", "TLS", "LoadConfig", "BoundImport",
    "IAT", "DelayImport", "CLR", "Reserved",
]

_SUBSYSTEM_NAMES = {
    0: "Unknown", 1: "Native", 2: "WindowsGUI", 3: "WindowsCUI",
    5: "OS2CUI", 7: "PosixCUI", 9: "WindowsCEGUI",
    10: "EFIApplication", 11: "EFIBootServiceDriver",
    12: "EFIRuntimeDriver", 13: "EFIROM", 14: "Xbox",
    16: "WindowsBootApplication",
}

_MACHINE_NAMES = {
    0x14c: "x86", 0x8664: "x64", 0xAA64: "ARM64", 0x1c0: "ARM",
    0x1c4: "ARMv7", 0x5032: "RISC-V 32", 0x5064: "RISC-V 64",
}


# ---------------------------------------------------------------------------
#  Parser
# ---------------------------------------------------------------------------

class PEParser:
    """Parse a PE binary from raw bytes."""

    def __init__(self, data: bytes):
        self.data = data
        self.sections: List[PESection] = []
        self.imports: List[ImportLibrary] = []
        self.exports: List[ExportEntry] = []
        self.data_dirs: List[DataDirectory] = []

        self.is_pe64 = False
        self.machine = 0
        self.machine_name = "unknown"
        self.num_sections = 0
        self.timestamp = 0
        self.entry_point_rva = 0
        self.image_base = 0
        self.section_alignment = 0
        self.file_alignment = 0
        self.subsystem = 0
        self.subsystem_name = "Unknown"
        self.dll_characteristics = 0
        self.size_of_image = 0
        self.size_of_headers = 0
        self.checksum = 0

        self._parse()

    # ---- helpers ----------------------------------------------------------

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

    def rva_to_offset(self, rva: int) -> Optional[int]:
        for sec in self.sections:
            if sec.virtual_address <= rva < sec.virtual_address + sec.raw_size:
                return rva - sec.virtual_address + sec.raw_offset
        return None

    # ---- main parse -------------------------------------------------------

    def _parse(self):
        if len(self.data) < 64:
            raise ValueError("File too small for PE")
        if self.data[:2] != b'MZ':
            raise ValueError("Not a PE file (missing MZ signature)")

        e_lfanew = self._read(0x3C, '<I')[0]
        if e_lfanew + 4 > len(self.data):
            raise ValueError("Invalid e_lfanew")
        sig = self.data[e_lfanew:e_lfanew + 4]
        if sig != b'PE\x00\x00':
            raise ValueError("Not a PE file (missing PE signature)")

        # COFF header (20 bytes after PE sig)
        coff_off = e_lfanew + 4
        (self.machine, self.num_sections, self.timestamp,
         _sym_off, _sym_count, opt_size, _chars) = self._read(coff_off, '<HHIIIHH')
        self.machine_name = _MACHINE_NAMES.get(self.machine, f"0x{self.machine:x}")

        # Optional header
        opt_off = coff_off + 20
        magic = self._read(opt_off, '<H')[0]
        self.is_pe64 = (magic == 0x20b)

        if self.is_pe64:
            self._parse_optional64(opt_off)
        else:
            self._parse_optional32(opt_off)

        # Sections
        sec_off = opt_off + opt_size
        self._parse_sections(sec_off)

        # Data directories → imports / exports
        self._parse_imports()
        self._parse_exports()

    def _parse_optional32(self, off: int):
        # PE32 Optional Header — read fields individually for reliability
        # Magic(H) MajLink(B) MinLink(B) SizeOfCode(I) SizeOfInitData(I)
        # SizeOfUninitData(I) EntryPointRVA(I) BaseOfCode(I) BaseOfData(I)
        # ImageBase(I) SectionAlign(I) FileAlign(I)
        # MajOS(H) MinOS(H) MajImg(H) MinImg(H) MajSub(H) MinSub(H)
        # Win32Ver(I) SizeOfImage(I) SizeOfHeaders(I) Checksum(I)
        # Subsystem(H) DllCharacteristics(H)
        self.entry_point_rva = self._read(off + 16, '<I')[0]
        self.image_base = self._read(off + 28, '<I')[0]
        self.section_alignment = self._read(off + 32, '<I')[0]
        self.file_alignment = self._read(off + 36, '<I')[0]
        self.size_of_image = self._read(off + 56, '<I')[0]
        self.size_of_headers = self._read(off + 60, '<I')[0]
        self.checksum = self._read(off + 64, '<I')[0]
        self.subsystem = self._read(off + 68, '<H')[0]
        self.dll_characteristics = self._read(off + 70, '<H')[0]
        self.subsystem_name = _SUBSYSTEM_NAMES.get(self.subsystem, str(self.subsystem))
        dd_off = off + 96
        self._parse_data_directories(dd_off)

    def _parse_optional64(self, off: int):
        # PE32+ (PE64) Optional Header — individual field reads
        self.entry_point_rva = self._read(off + 16, '<I')[0]
        self.image_base = self._read(off + 24, '<Q')[0]
        self.section_alignment = self._read(off + 32, '<I')[0]
        self.file_alignment = self._read(off + 36, '<I')[0]
        self.size_of_image = self._read(off + 56, '<I')[0]
        self.size_of_headers = self._read(off + 60, '<I')[0]
        self.checksum = self._read(off + 64, '<I')[0]
        self.subsystem = self._read(off + 68, '<H')[0]
        self.dll_characteristics = self._read(off + 70, '<H')[0]
        self.subsystem_name = _SUBSYSTEM_NAMES.get(self.subsystem, str(self.subsystem))
        dd_off = off + 112
        self._parse_data_directories(dd_off)

    def _parse_data_directories(self, off: int):
        for i in range(min(16, (len(self.data) - off) // 8)):
            rva, size = self._read(off + i * 8, '<II')
            name = _DATA_DIR_NAMES[i] if i < len(_DATA_DIR_NAMES) else f"Dir{i}"
            self.data_dirs.append(DataDirectory(name=name, rva=rva, size=size))

    def _parse_sections(self, off: int):
        for i in range(self.num_sections):
            s_off = off + i * 40
            if s_off + 40 > len(self.data):
                break
            raw_name = self.data[s_off:s_off + 8]
            name = raw_name.split(b'\x00', 1)[0].decode('ascii', errors='replace')
            (vsize, va, rsize, roff, _, _, _, _, chars,
             ) = self._read(s_off + 8, '<IIIIIIHHI')
            self.sections.append(PESection(
                name=name, virtual_address=va, virtual_size=vsize,
                raw_offset=roff, raw_size=rsize, characteristics=chars,
            ))

    # ---- imports ----------------------------------------------------------

    def _parse_imports(self):
        imp_dir = next((d for d in self.data_dirs if d.name == "Import"), None)
        if not imp_dir or imp_dir.rva == 0:
            return
        off = self.rva_to_offset(imp_dir.rva)
        if off is None:
            return

        ptr_size = 8 if self.is_pe64 else 4
        ptr_fmt = '<Q' if self.is_pe64 else '<I'
        ordinal_flag = 1 << 63 if self.is_pe64 else 1 << 31

        while True:
            if off + 20 > len(self.data):
                break
            (ilt_rva, _, _, name_rva, iat_rva) = self._read(off, '<IIIII')
            if ilt_rva == 0 and name_rva == 0:
                break

            name_off = self.rva_to_offset(name_rva)
            lib_name = self._read_cstring(name_off) if name_off else "?"

            lib = ImportLibrary(name=lib_name)
            # Prefer ILT; fall back to IAT
            thunk_rva = ilt_rva if ilt_rva else iat_rva
            thunk_off = self.rva_to_offset(thunk_rva)
            iat_entry_rva = iat_rva

            if thunk_off is not None:
                while True:
                    if thunk_off + ptr_size > len(self.data):
                        break
                    val = self._read(thunk_off, ptr_fmt)[0]
                    if val == 0:
                        break
                    if val & ordinal_flag:
                        ordinal = val & 0xFFFF
                        lib.functions.append(ImportFunction(
                            name=f"ordinal_{ordinal}", ordinal=ordinal,
                            iat_rva=iat_entry_rva,
                        ))
                    else:
                        hint_off = self.rva_to_offset(val & 0x7FFFFFFF)
                        if hint_off and hint_off + 2 < len(self.data):
                            hint = self._read(hint_off, '<H')[0]
                            fname = self._read_cstring(hint_off + 2)
                            lib.functions.append(ImportFunction(
                                name=fname, ordinal=0, hint=hint,
                                iat_rva=iat_entry_rva,
                            ))
                    thunk_off += ptr_size
                    iat_entry_rva += ptr_size

            self.imports.append(lib)
            off += 20

    # ---- exports ----------------------------------------------------------

    def _parse_exports(self):
        exp_dir = next((d for d in self.data_dirs if d.name == "Export"), None)
        if not exp_dir or exp_dir.rva == 0:
            return
        off = self.rva_to_offset(exp_dir.rva)
        if off is None:
            return

        (_, _, _, _name_rva, ordinal_base, num_funcs, num_names,
         funcs_rva, names_rva, ords_rva,
         ) = self._read(off, '<IIIIIIIII I')  # 10 DWORDs

        funcs_off = self.rva_to_offset(funcs_rva)
        names_off = self.rva_to_offset(names_rva)
        ords_off = self.rva_to_offset(ords_rva)

        name_map = {}
        if names_off and ords_off:
            for i in range(num_names):
                if names_off + i * 4 + 4 > len(self.data):
                    break
                if ords_off + i * 2 + 2 > len(self.data):
                    break
                name_rva = self._read(names_off + i * 4, '<I')[0]
                ordinal_idx = self._read(ords_off + i * 2, '<H')[0]
                noff = self.rva_to_offset(name_rva)
                if noff:
                    name_map[ordinal_idx] = self._read_cstring(noff)

        if funcs_off:
            for i in range(num_funcs):
                if funcs_off + i * 4 + 4 > len(self.data):
                    break
                func_rva = self._read(funcs_off + i * 4, '<I')[0]
                if func_rva == 0:
                    continue
                name = name_map.get(i, f"ordinal_{i + ordinal_base}")
                self.exports.append(ExportEntry(
                    name=name, ordinal=i + ordinal_base, rva=func_rva,
                ))

    # ---- public helpers ---------------------------------------------------

    def get_code_sections(self) -> List[PESection]:
        return [s for s in self.sections if s.is_executable]

    def get_section_data(self, section: PESection) -> bytes:
        return self.data[section.raw_offset:section.raw_offset + section.raw_size]

    def entry_point_va(self) -> int:
        return self.image_base + self.entry_point_rva

    def summary(self) -> dict:
        total_imports = sum(len(lib.functions) for lib in self.imports)
        return {
            "format": "PE64" if self.is_pe64 else "PE32",
            "machine": self.machine_name,
            "entry_point": f"0x{self.entry_point_va():x}",
            "entry_point_rva": f"0x{self.entry_point_rva:x}",
            "image_base": f"0x{self.image_base:x}",
            "subsystem": self.subsystem_name,
            "sections": len(self.sections),
            "imports": total_imports,
            "import_libraries": len(self.imports),
            "exports": len(self.exports),
            "size_of_image": f"0x{self.size_of_image:x}",
            "timestamp": self.timestamp,
            "checksum": f"0x{self.checksum:x}",
        }
