---
name: binary-debugger
description: >-
  Debug C/C++ programs at the assembly and machine-code level.
  Disassemble instructions, step through individual CPU instructions,
  inspect registers and raw memory, set breakpoints on addresses,
  patch live process memory, NOP out instructions, and modify binaries
  on disk. Works with GDB, LLDB, and CDB. All standard source-level
  debugging commands remain available.
---
# Binary Debugger Skill

Debug programs at the **assembly and machine-code level** — disassemble, step through individual CPU instructions, inspect registers, read/write raw memory, patch live processes, and modify binaries on disk.

> Extends the standard C/C++ debugger. All source-level commands (step_in, step_over, breakpoints, inspect, backtrace) remain available alongside the new assembly-level commands.

## Prerequisites

- Python 3.8+
- A working C/C++ debugger: **GDB**, **LLDB**, or **CDB** (Windows)
- No additional Python packages required (uses native debugger commands)

Check available toolchain:
```bash
python src/NeuralDebug/cpp_debug_session.py info
```

## Quick Start

### 1. Start the Assembly Debug Server

```bash
python src/NeuralDebug/asm_debug_session.py serve program.exe --port 5678 --daemonize
```

For source files (auto-compiles with debug symbols):
```bash
python src/NeuralDebug/asm_debug_session.py serve program.c --port 5678 --daemonize
```

### 2. Check Status

```bash
python src/NeuralDebug/asm_debug_session.py status --port 5678
```

### 3. Send Commands

```bash
python src/NeuralDebug/asm_debug_session.py cmd -p 5678 <command> [args]
```

### 4. Stop

```bash
python src/NeuralDebug/asm_debug_session.py stop --port 5678
```

## Assembly Commands

| Command | Alias | What it does |
|---------|-------|-------------|
| `disassemble [addr] [count]` | `dis`, `disas` | Disassemble instructions at PC or address |
| `stepi` | `si_asm` | Step one machine instruction (follow calls) |
| `nexti` | `ni_asm` | Step one instruction, skip over calls |
| `registers [all]` | `reg`, `regs` | Show CPU registers (default: common; `all` for full set) |
| `memory <addr> [len]` | `mem` | Read raw memory bytes as hex dump |
| `memory_write <addr> <hex>` | `mw` | Write raw bytes to process memory |
| `patch <addr> <hex>` | — | Write bytes with before/after comparison |
| `nop <addr> [count]` | — | NOP out bytes at address (x86: 0x90) |
| `b *0x401000` | — | Set breakpoint at raw address |
| `patch_file <file> <offset> <hex>` | — | Patch binary file on disk |

All standard source-level commands also work:

| Command | Alias | What it does |
|---------|-------|-------------|
| `start` | `s` | Start program execution |
| `continue` | `c` | Continue to next breakpoint |
| `step_in` | `si` | Step into function (source level) |
| `step_over` | `n` | Step over (source level) |
| `step_out` | `so` | Step out of function |
| `b <line>` | `break` | Set source-level breakpoint |
| `b <func>` | `break` | Set breakpoint at function |
| `inspect` | `i` | Show current state |
| `evaluate <expr>` | `e` | Evaluate expression |
| `backtrace` | `bt` | Show call stack |
| `list` | `l` | Show source code |
| `quit` | `q` | End session |

## Command Details

### disassemble

View assembly instructions at the current position or at a specific address:

```bash
# 20 instructions from current PC (default)
cmd disassemble

# 40 instructions from current PC
cmd disassemble 40

# 20 instructions from a specific address
cmd disassemble 0x401000

# 30 instructions from a specific address
cmd disassemble 0x401000 30
```

**Response includes:**
- Address, instruction mnemonic, operands
- Function name and offset (if symbols available)
- Structured `disassembly` array for programmatic use

### stepi / nexti

Step one machine instruction at a time:

```bash
# Step into the next instruction (follows calls)
cmd stepi

# Step over the next instruction (skips calls)
cmd nexti
```

Both commands return the new position plus a 3-instruction disassembly context showing what's coming next.

### registers

Show CPU register values:

```bash
# Common registers (rax, rbx, ..., rip, eflags)
cmd registers

# All registers (including SSE, AVX, segment registers)
cmd registers all
```

**Returns:** Register name-value map. Values are in hex format.

### memory / memory_write

Read and write raw memory:

```bash
# Read 64 bytes at address (default length)
cmd memory 0x7fffffffde00

# Read 256 bytes
cmd memory 0x7fffffffde00 256

# Write bytes to memory
cmd memory_write 0x401050 90909090
```

**Memory read returns:** Classic hex dump format with address, hex values, and ASCII representation.

### patch

Write bytes to a live process with before/after comparison and automatic disassembly of the patched region:

```bash
# Patch 2 bytes (e.g., NOP out a jump)
cmd patch 0x401050 9090

# Patch a conditional jump to unconditional
cmd patch 0x401050 eb
```

**Returns:**
- Original bytes at the address
- New bytes written
- Disassembly of the patched region

### nop

Shortcut to write NOP instructions (0x90 on x86):

```bash
# NOP one byte
cmd nop 0x401050

# NOP 5 bytes (e.g., overwrite a call instruction)
cmd nop 0x401050 5
```

### Address Breakpoints

Set breakpoints on raw addresses (works without symbols):

```bash
# Break at address
cmd b *0x401000

# Break at function (standard)
cmd b main

# Break at file:line (standard)
cmd b main.c:42
```

### patch_file

Modify a binary file on disk (not the running process). Creates a `.bak` backup automatically:

```bash
# Patch at file offset 0x1a40
cmd patch_file program.exe 0x1a40 9090

# Patch at decimal offset
cmd patch_file program.exe 6720 eb08
```

## Typical Workflows

### Workflow 1: Analyse a Stripped Binary

```bash
# Start server on stripped executable
cmd serve stripped_binary --port 5678 --daemonize

# Set breakpoint at entry point
cmd b *0x401000
cmd start

# Disassemble to understand the code
cmd disassemble 50

# Step through instructions
cmd stepi
cmd stepi
cmd nexti  # skip over a call

# Check registers after a computation
cmd registers

# Read a memory buffer
cmd memory 0x7fffffffde00 128
```

### Workflow 2: Patch a Bug at Runtime

```bash
# Find the buggy instruction
cmd disassemble 0x401050 10

# NOP out a broken bounds check (5-byte call)
cmd nop 0x401050 5

# Verify the patch
cmd disassemble 0x401050 10

# Continue execution with the fix
cmd continue
```

### Workflow 3: Patch a Binary on Disk

```bash
# Identify the problem in the running process
cmd disassemble 0x401200 10
# → 0x401200: jne 0x401220  (this should be je)

# Find the file offset (calculate from section base)
# Patch the binary: change 0x75 (jne) to 0x74 (je)
cmd patch_file program.exe 0x600 74

# Restart with the fixed binary
cmd quit
cmd serve program.exe --port 5678 --daemonize
```

### Workflow 4: Reverse Engineer a Function

```bash
# Disassemble a large block
cmd disassemble 0x401000 100

# Read string data referenced by the code
cmd memory 0x402000 256

# Check what a function returns
cmd b *0x4010ff   # break at ret instruction
cmd continue
cmd registers      # check rax (return value on x86-64)
```

## Debugger Backend Mapping

| Command | GDB MI | LLDB | CDB |
|---------|--------|------|-----|
| `disassemble` | `-data-disassemble` | `disassemble --pc` | `u @$ip L20` |
| `stepi` | `-exec-step-instruction` | `thread step-inst` | `t` |
| `nexti` | `-exec-next-instruction` | `thread step-inst-over` | `p` |
| `registers` | `-data-list-register-values` | `register read` | `r` |
| `memory` | `-data-read-memory-bytes` | `memory read` | `db <addr> L<n>` |
| `memory_write` | `-data-write-memory-bytes` | `memory write` | `eb <addr>` |
| `b *addr` | `-break-insert *addr` | `breakpoint set -a addr` | `bp addr` |

## Architecture

```
src/NeuralDebug/
├── asm_debug_session.py              # CLI entry point (serve/cmd/status/stop)
├── debuggers/
│   ├── asm_gdb.py                    # GDB assembly extension (GdbAsmDebugger)
│   ├── asm_lldb.py                   # LLDB assembly extension (LldbAsmDebugger)
│   ├── asm_cdb.py                    # CDB assembly extension (CdbAsmDebugger)
│   ├── asm_common.py                 # AsmDebugServer + BinaryPatcher + factory
│   ├── cpp_gdb.py                    # Base GDB backend (unchanged)
│   ├── cpp_lldb.py                   # Base LLDB backend (unchanged)
│   ├── cpp_cdb.py                    # Base CDB backend (unchanged)
│   └── cpp_common.py                 # Toolchain detection, compilation (unchanged)
└── debug_common.py                   # Base server, MI transport (unchanged)
```

**No existing files are modified.** The assembly backends subclass the existing debugger classes.

## Response Format

All commands return JSON with at minimum:
```json
{
  "status": "paused|running|completed|error",
  "command": "command_name args",
  "message": "Human-readable result",
  "current_location": {"file": "...", "line": 0, "function": "...", "code_context": "..."},
  "call_stack": [],
  "local_variables": {},
  "stdout_new": "",
  "stderr_new": ""
}
```

Assembly commands add extra fields:
- `disassembly`: Array of `{address, instruction, function, offset}` objects
- `registers`: Map of register name → hex value
- `memory`: `{address, length, hex, ascii}` object
- `patch`: `{address, original, new, size}` object
- `bytes_written`: Integer count of bytes written

## Tips

- **x86 NOP is `0x90`** — the `nop` command uses this automatically
- **`patch` shows before/after** — use it instead of `memory_write` when you want verification
- **Address breakpoints use `*` prefix** — same syntax as GDB: `b *0x401000`
- **Hex bytes have no `0x` prefix** — `patch 0x401000 9090` not `0x9090`
- **File offsets ≠ memory addresses** — file offsets are positions in the binary file; memory addresses are runtime virtual addresses. Use the executable's section headers to convert between them.
- **Maximum memory read is 4096 bytes** — for larger reads, make multiple calls
- **NOP maximum is 256 bytes** — for larger regions, use `patch` directly
