"""GDB assembly-level debugging extension.

Subclasses GdbDebugger to add:
- Disassembly (``-data-disassemble``)
- Instruction-level stepping (``-exec-step-instruction``, ``-exec-next-instruction``)
- Register inspection (``-data-list-register-names``, ``-data-list-register-values``)
- Raw memory read/write (``-data-read-memory-bytes``, ``-data-write-memory-bytes``)
- Address-based breakpoints (``*0xADDR``)
- NOP patching and live binary patching
"""

import re
from typing import Dict, List, Optional

from debuggers.cpp_gdb import GdbDebugger


class GdbAsmDebugger(GdbDebugger):
    """GDB debugger extended with assembly / machine-code commands."""

    # ------------------------------------------------------------------
    # disassemble [addr] [count]
    # ------------------------------------------------------------------
    def cmd_disassemble(self, args: str) -> dict:
        """Disassemble instructions.

        Usage:
            disassemble              — 20 instructions from $pc
            disassemble 30           — 30 instructions from $pc
            disassemble 0x401000     — 20 instructions from address
            disassemble 0x401000 40  — 40 instructions from address
        """
        parts = args.strip().split()
        addr = "$pc"
        count = 20

        if len(parts) >= 1:
            if parts[0].startswith("0x") or parts[0].startswith("0X"):
                addr = parts[0]
                if len(parts) >= 2:
                    try:
                        count = int(parts[1])
                    except ValueError:
                        pass
            else:
                try:
                    count = int(parts[0])
                except ValueError:
                    addr = parts[0]

        # Use mode 0 = pure disassembly (no mixed source)
        # end address = start + count * 16 (generous upper bound)
        byte_range = count * 16
        tok = self._send_mi(
            f'-data-disassemble -s {addr} -e "{addr}+{byte_range}" -- 0'
        )
        result, _ = self._collect_until_result(tok, timeout=10)

        if result and result.get('class_') == 'done':
            asm_insns = result.get('body', {}).get('asm_insns', [])
            if not asm_insns:
                # Try alternate key name
                asm_insns = result.get('body', {}).get('asm', [])

            lines = []
            asm_data = []
            for i, item in enumerate(asm_insns):
                if i >= count:
                    break
                if isinstance(item, dict):
                    address = item.get('address', '?')
                    inst = item.get('inst', '')
                    func_name = item.get('func-name', '')
                    offset = item.get('offset', '')
                    entry = {
                        "address": address,
                        "instruction": inst,
                        "function": func_name,
                        "offset": offset,
                    }
                    asm_data.append(entry)
                    label = f"{func_name}+{offset}" if func_name and offset else ""
                    if label:
                        lines.append(f"  {address} <{label}>:\t{inst}")
                    else:
                        lines.append(f"  {address}:\t{inst}")

            msg = f"Disassembly ({len(asm_data)} instructions):\n" + "\n".join(lines)
            return {
                "status": "paused",
                "command": f"disassemble {args}",
                "message": msg,
                "disassembly": asm_data,
                "current_location": self._get_current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            }

        if result and result.get('class_') == 'error':
            errmsg = result.get('body', {}).get('msg', 'Disassembly failed')
            return self._error(f"Disassembly error: {errmsg}")
        return self._error("Disassembly timed out")

    # ------------------------------------------------------------------
    # stepi — step one machine instruction (follow calls)
    # ------------------------------------------------------------------
    def cmd_stepi(self) -> dict:
        tok = self._send_mi('-exec-step-instruction')
        result, _ = self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=30)
        if stop:
            resp = self._build_stop_response("stepi", stop)
            # Append disassembly at current PC
            dis = self._quick_disassemble(3)
            if dis:
                resp["message"] += "\n" + dis
                resp["disassembly_context"] = dis
            return resp
        return self._error("No stop event after stepi")

    # ------------------------------------------------------------------
    # nexti — step one instruction, skip over calls
    # ------------------------------------------------------------------
    def cmd_nexti(self) -> dict:
        tok = self._send_mi('-exec-next-instruction')
        result, _ = self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=30)
        if stop:
            resp = self._build_stop_response("nexti", stop)
            dis = self._quick_disassemble(3)
            if dis:
                resp["message"] += "\n" + dis
                resp["disassembly_context"] = dis
            return resp
        return self._error("No stop event after nexti")

    # ------------------------------------------------------------------
    # registers — show all CPU registers
    # ------------------------------------------------------------------
    def cmd_registers(self, args: str = "") -> dict:
        # Get register names
        tok = self._send_mi('-data-list-register-names')
        names_result, _ = self._collect_until_result(tok, timeout=5)
        reg_names: List[str] = []
        if names_result and names_result.get('class_') == 'done':
            reg_names = names_result.get('body', {}).get('register-names', [])
            if isinstance(reg_names, list):
                reg_names = [n if isinstance(n, str) else '' for n in reg_names]

        # Get register values (hex format)
        tok = self._send_mi('-data-list-register-values x')
        vals_result, _ = self._collect_until_result(tok, timeout=5)

        registers: Dict[str, str] = {}
        if vals_result and vals_result.get('class_') == 'done':
            reg_list = vals_result.get('body', {}).get('register-values', [])
            for item in reg_list:
                if isinstance(item, dict):
                    idx_str = item.get('number', '')
                    value = item.get('value', '?')
                    try:
                        idx = int(idx_str)
                        name = reg_names[idx] if idx < len(reg_names) else f"r{idx}"
                    except (ValueError, IndexError):
                        name = f"r{idx_str}"
                    if name:  # skip empty-named registers
                        registers[name] = value

        # Filter to important registers if no args, or show all
        if args.strip().lower() in ("all", "--all", "-a"):
            display = registers
        else:
            # Show commonly useful registers first
            important = [
                'rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'rsp',
                'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15',
                'rip', 'eflags', 'cs', 'ss', 'ds', 'es', 'fs', 'gs',
                # 32-bit fallback
                'eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp', 'eip',
            ]
            display = {}
            for name in important:
                if name in registers:
                    display[name] = registers[name]
            # If none matched (e.g., ARM), show all
            if not display:
                display = registers

        lines = []
        for name, value in display.items():
            lines.append(f"  {name:8s} = {value}")
        msg = f"Registers ({len(display)}):\n" + "\n".join(lines)

        return {
            "status": "paused",
            "command": f"registers {args}".strip(),
            "message": msg,
            "registers": registers,
            "current_location": self._get_current_location(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # memory <addr> [length] — read raw memory bytes
    # ------------------------------------------------------------------
    def cmd_memory_read(self, args: str) -> dict:
        parts = args.strip().split()
        if not parts:
            return self._error("Usage: memory <address> [length]\n"
                               "  e.g.: memory 0x7fffffffde00 64")
        addr = parts[0]
        length = 64
        if len(parts) >= 2:
            try:
                length = int(parts[1])
            except ValueError:
                return self._error(f"Invalid length: {parts[1]}")
        length = min(length, 4096)

        tok = self._send_mi(f'-data-read-memory-bytes {addr} {length}')
        result, _ = self._collect_until_result(tok, timeout=10)

        if result and result.get('class_') == 'done':
            memory = result.get('body', {}).get('memory', [])
            hex_data = ""
            begin_addr = addr
            for block in memory:
                if isinstance(block, dict):
                    begin_addr = block.get('begin', addr)
                    hex_data = block.get('contents', '')

            # Format as hex dump
            raw_bytes = bytes.fromhex(hex_data) if hex_data else b''
            lines = self._format_hexdump(int(begin_addr, 16) if begin_addr.startswith('0x')
                                         else int(begin_addr), raw_bytes)
            msg = f"Memory at {addr} ({len(raw_bytes)} bytes):\n" + "\n".join(lines)
            return {
                "status": "paused",
                "command": f"memory {args}",
                "message": msg,
                "memory": {
                    "address": begin_addr,
                    "length": len(raw_bytes),
                    "hex": hex_data,
                    "ascii": ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw_bytes),
                },
                "current_location": self._get_current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            }

        if result and result.get('class_') == 'error':
            errmsg = result.get('body', {}).get('msg', 'Memory read failed')
            return self._error(f"Memory read error: {errmsg}")
        return self._error("Memory read timed out")

    # ------------------------------------------------------------------
    # memory_write <addr> <hex_bytes> — write raw bytes to memory
    # ------------------------------------------------------------------
    def cmd_memory_write(self, args: str) -> dict:
        parts = args.strip().split()
        if len(parts) < 2:
            return self._error("Usage: memory_write <address> <hex_bytes>\n"
                               "  e.g.: memory_write 0x401000 90909090")
        addr = parts[0]
        hex_bytes = ''.join(parts[1:]).replace(' ', '')
        # Validate hex
        try:
            raw = bytes.fromhex(hex_bytes)
        except ValueError:
            return self._error(f"Invalid hex bytes: {hex_bytes}")

        tok = self._send_mi(f'-data-write-memory-bytes {addr} {hex_bytes}')
        result, _ = self._collect_until_result(tok, timeout=5)

        if result and result.get('class_') == 'done':
            return {
                "status": "paused",
                "command": f"memory_write {args}",
                "message": f"Wrote {len(raw)} bytes at {addr}: {hex_bytes}",
                "bytes_written": len(raw),
                "current_location": self._get_current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            }
        if result and result.get('class_') == 'error':
            errmsg = result.get('body', {}).get('msg', 'Memory write failed')
            return self._error(f"Memory write error: {errmsg}")
        return self._error("Memory write timed out")

    # ------------------------------------------------------------------
    # patch <addr> <hex_bytes> — alias for memory_write with before/after
    # ------------------------------------------------------------------
    def cmd_patch(self, args: str) -> dict:
        parts = args.strip().split()
        if len(parts) < 2:
            return self._error("Usage: patch <address> <hex_bytes>\n"
                               "  e.g.: patch 0x401000 9090")
        addr = parts[0]
        hex_bytes = ''.join(parts[1:]).replace(' ', '')
        try:
            new_bytes = bytes.fromhex(hex_bytes)
        except ValueError:
            return self._error(f"Invalid hex bytes: {hex_bytes}")

        # Read original bytes first
        tok = self._send_mi(f'-data-read-memory-bytes {addr} {len(new_bytes)}')
        result, _ = self._collect_until_result(tok, timeout=5)
        original_hex = ""
        if result and result.get('class_') == 'done':
            memory = result.get('body', {}).get('memory', [])
            for block in memory:
                if isinstance(block, dict):
                    original_hex = block.get('contents', '')

        # Write new bytes
        write_result = self.cmd_memory_write(args)
        if write_result.get('status') == 'error':
            return write_result

        # Disassemble the patched area
        dis_lines = self._quick_disassemble(5, addr)

        msg = (f"Patched {len(new_bytes)} bytes at {addr}\n"
               f"  Before: {original_hex}\n"
               f"  After:  {hex_bytes}")
        if dis_lines:
            msg += f"\n\nDisassembly after patch:\n{dis_lines}"

        return {
            "status": "paused",
            "command": f"patch {args}",
            "message": msg,
            "patch": {
                "address": addr,
                "original": original_hex,
                "new": hex_bytes,
                "size": len(new_bytes),
            },
            "current_location": self._get_current_location(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # nop <addr> [count] — NOP out instructions
    # ------------------------------------------------------------------
    def cmd_nop(self, args: str) -> dict:
        parts = args.strip().split()
        if not parts:
            return self._error("Usage: nop <address> [byte_count]\n"
                               "  e.g.: nop 0x401000 5")
        addr = parts[0]
        count = 1
        if len(parts) >= 2:
            try:
                count = int(parts[1])
            except ValueError:
                return self._error(f"Invalid count: {parts[1]}")
        count = min(count, 256)

        nop_hex = "90" * count  # x86 NOP
        return self.cmd_patch(f"{addr} {nop_hex}")

    # ------------------------------------------------------------------
    # Override breakpoint to support *addr syntax
    # ------------------------------------------------------------------
    def cmd_set_breakpoint(self, args: str) -> dict:
        parts = args.strip().split(None, 1)
        if not parts:
            return self._error("Usage: b <line> | b <func> | b *<address>")

        location = parts[0]
        if location.startswith('*'):
            # Address breakpoint — pass directly to GDB
            mi_cmd = f'-break-insert {location}'
            tok = self._send_mi(mi_cmd)
            result, _ = self._collect_until_result(tok, timeout=5)

            if result and result.get('class_') == 'error':
                msg = result.get('body', {}).get('msg', 'Failed')
                return self._error(f"Failed to set breakpoint: {msg}")

            bkpt = result.get('body', {}).get('bkpt', {}) if result else {}
            bp_num = bkpt.get('number', '?')
            bp_addr = bkpt.get('addr', location)
            bp_func = bkpt.get('func', '')

            msg = f"Breakpoint {bp_num} at address {bp_addr}"
            if bp_func:
                msg += f" in {bp_func}()"

            return {
                "status": "running" if self.is_started and not self.is_paused else "paused",
                "command": f"set_breakpoint {args}",
                "message": msg,
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            }

        # Fall back to parent for source-level breakpoints
        return super().cmd_set_breakpoint(args)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _quick_disassemble(self, count: int = 5, addr: str = "$pc") -> str:
        """Return a short disassembly string for context."""
        byte_range = count * 16
        tok = self._send_mi(
            f'-data-disassemble -s {addr} -e "{addr}+{byte_range}" -- 0'
        )
        result, _ = self._collect_until_result(tok, timeout=5)
        if result and result.get('class_') == 'done':
            insns = result.get('body', {}).get('asm_insns', [])
            lines = []
            for i, item in enumerate(insns):
                if i >= count:
                    break
                if isinstance(item, dict):
                    a = item.get('address', '?')
                    inst = item.get('inst', '')
                    lines.append(f"  {a}:\t{inst}")
            return "\n".join(lines)
        return ""

    @staticmethod
    def _format_hexdump(start_addr: int, data: bytes) -> List[str]:
        """Format bytes as a classic hex dump (16 bytes per line)."""
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_part = ' '.join(f'{b:02x}' for b in chunk)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            addr = f"0x{start_addr + offset:08x}"
            lines.append(f"  {addr}  {hex_part:<48s}  |{ascii_part}|")
        return lines
