"""String extractor for binary analysis.

Finds ASCII and UTF-16LE strings in binary data with configurable
minimum length.  Reports each string's file offset, virtual address
(if section mapping is available), and the containing section name.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class ExtractedString:
    offset: int
    value: str
    encoding: str          # "ascii" or "utf-16le"
    length: int
    section: str = ""
    virtual_address: int = 0


class StringExtractor:
    """Extract readable strings from raw binary data."""

    # Printable ASCII range (space through tilde, plus tab/newline)
    _ASCII_RE = re.compile(rb'[\x20-\x7e\t]{4,}')
    _UTF16_RE = re.compile(rb'(?:[\x20-\x7e]\x00){4,}')

    def __init__(self, data: bytes, min_length: int = 4):
        self.data = data
        self.min_length = min_length

    def extract_ascii(self) -> List[ExtractedString]:
        pattern = re.compile(
            rb'[\x20-\x7e\t]{' + str(self.min_length).encode() + rb',}'
        )
        results = []
        for m in pattern.finditer(self.data):
            val = m.group().decode('ascii', errors='replace')
            results.append(ExtractedString(
                offset=m.start(), value=val,
                encoding="ascii", length=len(val),
            ))
        return results

    def extract_utf16(self) -> List[ExtractedString]:
        min_bytes = self.min_length * 2
        pattern = re.compile(
            rb'(?:[\x20-\x7e]\x00){' + str(self.min_length).encode() + rb',}'
        )
        results = []
        for m in pattern.finditer(self.data):
            raw = m.group()
            val = raw.decode('utf-16-le', errors='replace')
            results.append(ExtractedString(
                offset=m.start(), value=val,
                encoding="utf-16le", length=len(val),
            ))
        return results

    def extract_all(self) -> List[ExtractedString]:
        strings = self.extract_ascii() + self.extract_utf16()
        # Deduplicate overlapping utf-16 hits that are also ascii
        seen_offsets = set()
        unique = []
        for s in sorted(strings, key=lambda x: x.offset):
            if s.offset not in seen_offsets:
                unique.append(s)
                seen_offsets.add(s.offset)
        return unique

    @staticmethod
    def annotate_sections(strings: List[ExtractedString],
                          sections: list) -> List[ExtractedString]:
        """Add section name and VA to each string.

        ``sections`` should be a list of objects with ``name``,
        ``raw_offset``, ``raw_size``, and ``virtual_address`` attributes
        (works with both PESection and ELFSection).
        """
        for s in strings:
            for sec in sections:
                raw_off = getattr(sec, 'raw_offset', getattr(sec, 'offset', 0))
                raw_sz = getattr(sec, 'raw_size', getattr(sec, 'size', 0))
                va_base = getattr(sec, 'virtual_address', getattr(sec, 'address', 0))
                if raw_off <= s.offset < raw_off + raw_sz:
                    s.section = sec.name
                    s.virtual_address = va_base + (s.offset - raw_off)
                    break
        return strings

    def summary(self, strings: Optional[List[ExtractedString]] = None) -> dict:
        if strings is None:
            strings = self.extract_all()
        ascii_count = sum(1 for s in strings if s.encoding == "ascii")
        utf16_count = sum(1 for s in strings if s.encoding == "utf-16le")
        avg_len = sum(s.length for s in strings) / len(strings) if strings else 0
        longest = max(strings, key=lambda s: s.length) if strings else None
        return {
            "total": len(strings),
            "ascii": ascii_count,
            "utf16": utf16_count,
            "avg_length": round(avg_len, 1),
            "longest": longest.value[:80] if longest else "",
            "longest_length": longest.length if longest else 0,
        }
