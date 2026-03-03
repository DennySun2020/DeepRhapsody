---
name: reverse-engineering
description: >-
  Static binary analysis for reverse engineering. Parse PE/ELF headers,
  extract strings, discover functions in stripped binaries, build
  cross-reference maps, generate control flow graphs, analyze entropy
  for packing detection, and perform static disassembly. Works with
  .exe, .dll, .so, .elf, and raw binaries. Pure Python — no external
  dependencies required.
---
# Reverse Engineering Skill

**Static binary analysis** — parse headers, extract strings, discover functions,
build cross-references, generate control flow graphs, and detect packing.
All analysis is performed statically (no debugger or execution required).

> Complements the binary-debugger skill: use RE for static analysis, then
> switch to binary-debugger for dynamic runtime debugging.

## Prerequisites

- Python 3.8+
- No additional packages required (uses only `struct`, `re`, `math`)

## Quick Start

### 1. Start the RE Server

```bash
python src/NeuralDebug/re_session.py serve program.exe --port 5695 --daemonize
```

### 2. Check Status

```bash
python src/NeuralDebug/re_session.py status --port 5695
```

### 3. Send Commands

```bash
python src/NeuralDebug/re_session.py cmd -p 5695 info
python src/NeuralDebug/re_session.py cmd -p 5695 imports
python src/NeuralDebug/re_session.py cmd -p 5695 strings
python src/NeuralDebug/re_session.py cmd -p 5695 functions
```

### 4. Stop

```bash
python src/NeuralDebug/re_session.py stop --port 5695
```

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `info` | — | Binary overview: format, arch, entry point, size, sections, imports |
| `headers` | — | Detailed PE/ELF header information (data directories, segments) |
| `sections` | `secs` | List all sections with permissions (RWX) and entropy |
| `imports` | `imp` | Imported functions grouped by library |
| `exports` | `exp` | Exported function names and addresses |
| `strings [min] [limit]` | `str` | Extract ASCII/UTF-16 strings (default: min=4, limit=500) |
| `functions` | `funcs`, `fn` | Discover function boundaries (prologue + call target analysis) |
| `disassemble [addr] [n]` | `dis`, `disas` | Static disassembly (default: entry point, 20 instructions) |
| `xrefs [address]` | `xref`, `x` | Cross-references: summary or to/from a specific address |
| `cfg <address> [format]` | — | Control flow graph: `ascii`, `mermaid`, or `json` |
| `entropy` | — | Per-section entropy analysis (detect packing/encryption) |
| `hexdump [offset] [len]` | `hex`, `hd` | Raw hex dump at file offset (default: 256 bytes from 0) |
| `help` | — | List available commands |
| `ping` | — | Check server is alive |
| `quit` | — | Stop the server |

## Supported Formats

| Format | Extensions | Parsing |
|--------|-----------|---------|
| **PE32** (x86) | `.exe`, `.dll`, `.sys` | Headers, sections, imports (IAT), exports, data directories |
| **PE64** (x64/ARM64) | `.exe`, `.dll`, `.sys` | Same as PE32 + 64-bit addresses |
| **ELF32** | `.so`, `.o`, `.elf`, no ext | Headers, sections, segments, symbols (.symtab/.dynsym), dynamic (DT_NEEDED) |
| **ELF64** | `.so`, `.o`, `.elf`, no ext | Same as ELF32 + 64-bit |
| **Raw binary** | `.bin`, any | Hexdump and string extraction only |

## Analysis Capabilities

### 1. Binary Overview (`info`)

Returns format, architecture, entry point, image base, subsystem, section count,
import/export counts, and file size.

```json
{
  "format": "PE32",
  "arch": "x86",
  "entry_point": "0x401136",
  "image_base": "0x400000",
  "subsystem": "WindowsCUI",
  "sections": 7,
  "imports": 88,
  "exports": 0
}
```

### 2. Import Analysis (`imports`)

Lists all imported functions grouped by library, with IAT addresses and hint values.
Essential for understanding what APIs a binary uses.

```json
{
  "library": "KERNEL32.dll",
  "count": 88,
  "functions": [
    {"name": "CreateFileW", "hint": 230, "iat_rva": "0x7d158"},
    {"name": "WriteFile", "hint": 1615, "iat_rva": "0x7d084"}
  ]
}
```

### 3. String Extraction (`strings`)

Finds ASCII and UTF-16LE strings with section and virtual address mapping.
Useful for identifying error messages, file paths, URLs, and format strings.

```bash
# Find strings at least 8 characters long, show first 100
python re_session.py cmd -p 5695 strings 8 100
```

### 4. Function Discovery (`functions`)

Combines multiple heuristics to locate function boundaries in stripped binaries:

| Source | Method |
|--------|--------|
| **call_target** | Decode all CALL instructions, collect their targets |
| **prologue** | Scan for `push ebp; mov ebp,esp` and similar patterns |
| **export** | PE export table entries |
| **entry_point** | Binary's declared entry point |
| **symbol** | ELF .symtab/.dynsym entries |

### 5. Cross-References (`xrefs`)

Builds a map of code-to-code references (calls and jumps).

```bash
# Overall summary
python re_session.py cmd -p 5695 xrefs
# → {"total_xrefs": 23133, "by_type": {"call": 5807, "jump": 17326}}

# Refs to/from a specific address
python re_session.py cmd -p 5695 xrefs 0x401050
```

### 6. Control Flow Graph (`cfg`)

Builds a CFG for a function by splitting code into basic blocks at
branch/jump/call boundaries.

```bash
# ASCII art
python re_session.py cmd -p 5695 cfg 0x401000

# Mermaid diagram (for docs/wikis)
python re_session.py cmd -p 5695 cfg 0x401000 mermaid

# JSON (for programmatic use)
python re_session.py cmd -p 5695 cfg 0x401000 json
```

### 7. Entropy Analysis (`entropy`)

Calculates Shannon entropy per section. High entropy (>6.8) suggests
packed, encrypted, or compressed data.

```json
{
  "overall_entropy": 5.82,
  "possibly_packed": false,
  "suspicious_sections": []
}
```

### 8. Static Disassembly (`disassemble`)

Decodes x86/x64 instructions statically, identifying instruction boundaries
and control-flow targets (calls, jumps, returns).

```bash
python re_session.py cmd -p 5695 disassemble 0x401000 30
```

## Architecture

```
src/NeuralDebug/
├── re_session.py                 # CLI entry point + TCP server
└── reversing/
    ├── __init__.py
    ├── binary_analyzer.py        # Main orchestrator (ties everything together)
    ├── pe_parser.py              # PE32/PE64 format parser
    ├── elf_parser.py             # ELF32/ELF64 format parser
    ├── string_extractor.py       # ASCII/UTF-16 string extraction
    ├── x86_decoder.py            # Instruction length decoder + control flow
    ├── func_finder.py            # Function boundary detection
    ├── xref_engine.py            # Cross-reference analysis
    └── cfg_builder.py            # Control flow graph construction
```

### Design Principles

- **Pure Python** — no external dependencies (pefile, capstone, etc.)
- **Lazy analysis** — functions, xrefs, and CFGs are computed on first request
- **Separate from debugger** — no overlap with existing debug skills
- **Same TCP protocol** — reuses `send_command` from `debug_common.py`

## Typical Workflow

```bash
# 1. Start analysis
python re_session.py serve unknown.exe -p 5695 --daemonize

# 2. Get overview
python re_session.py cmd -p 5695 info
# → PE32, x86, WindowsCUI, 7 sections, 88 imports

# 3. Check if packed
python re_session.py cmd -p 5695 entropy
# → overall 5.82, not packed

# 4. Look at imports — what APIs does it use?
python re_session.py cmd -p 5695 imports
# → KERNEL32.dll: CreateFileW, WriteFile, ReadFile...

# 5. Find interesting strings
python re_session.py cmd -p 5695 strings 8
# → "Error: file not found", "http://...", format strings

# 6. Discover functions
python re_session.py cmd -p 5695 functions
# → 2012 functions (1300 from calls, 711 from prologues)

# 7. Analyze a specific function
python re_session.py cmd -p 5695 disassemble 0x4671f0 30
python re_session.py cmd -p 5695 cfg 0x4671f0

# 8. Find who calls a function
python re_session.py cmd -p 5695 xrefs 0x401050

# 9. Done
python re_session.py stop -p 5695
```

## Comparison: RE Skill vs Binary-Debugger Skill

| Capability | RE Skill | Binary-Debugger |
|-----------|----------|-----------------|
| PE/ELF header parsing | ✅ | ❌ |
| Import/export analysis | ✅ | ❌ |
| String extraction | ✅ | ❌ |
| Function discovery | ✅ (static) | ❌ |
| Cross-references | ✅ | ❌ |
| Control flow graph | ✅ | ❌ |
| Entropy / packing detection | ✅ | ❌ |
| Static disassembly | ✅ (x86/x64) | ❌ |
| Live disassembly | ❌ | ✅ |
| Instruction stepping | ❌ | ✅ (stepi/nexti) |
| Register inspection | ❌ | ✅ |
| Memory read/write | ❌ | ✅ |
| Live patching | ❌ | ✅ |
| Binary patching | ❌ | ✅ (patch_file) |
| Breakpoints | ❌ | ✅ |
| Requires debugger | No | Yes (GDB/LLDB/CDB) |
| Requires execution | No | Yes |

Use both together for comprehensive analysis: RE for understanding structure,
binary-debugger for runtime behavior.
