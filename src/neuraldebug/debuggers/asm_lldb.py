"""LLDB assembly-level debugging extension.

Subclasses LldbDebugger to add assembly/machine-code commands.
LLDB uses its native CLI protocol (not MI), so commands are sent
as text and parsed from text output.
"""

import re
from typing import Dict, List, Optional

from debuggers.cpp_lldb import LldbDebugger


class LldbAsmDebugger(LldbDebugger):
    """LLDB debugger extended with assembly / machine-code commands."""

    # ------------------------------------------------------------------
    # disassemble
    # ------------------------------------------------------------------
    def cmd_disassemble(self, args: str) -> dict:
        parts = args.strip().split()
        addr = None
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

        if addr:
            self._send(f'disassemble --start-address {addr} --count {count}')
        else:
            self._send(f'disassemble --pc --count {count}')
        output = self._collect_output(timeout=10)

        asm_data = self._parse_disassembly(output)
        return {
            "status": "paused",
            "command": f"disassemble {args}",
            "message": f"Disassembly ({len(asm_data)} instructions):\n{output}",
            "disassembly": asm_data,
            "current_location": self._get_frame_info(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # stepi
    # ------------------------------------------------------------------
    def cmd_stepi(self) -> dict:
        self._send('thread step-inst')
        output = self._collect_output(timeout=30)
        loc = self._parse_stop_output(output)
        dis = self._quick_disassemble(3)
        msg = self._format_stop_message(output)
        if dis:
            msg += "\n" + dis
        return {
            "status": "paused",
            "command": "stepi",
            "message": msg,
            "current_location": loc,
            "call_stack": self._get_bt(),
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # nexti
    # ------------------------------------------------------------------
    def cmd_nexti(self) -> dict:
        self._send('thread step-inst-over')
        output = self._collect_output(timeout=30)
        loc = self._parse_stop_output(output)
        dis = self._quick_disassemble(3)
        msg = self._format_stop_message(output)
        if dis:
            msg += "\n" + dis
        return {
            "status": "paused",
            "command": "nexti",
            "message": msg,
            "current_location": loc,
            "call_stack": self._get_bt(),
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # registers
    # ------------------------------------------------------------------
    def cmd_registers(self, args: str = "") -> dict:
        if args.strip().lower() in ("all", "--all", "-a"):
            self._send('register read --all')
        else:
            self._send('register read')
        output = self._collect_output(timeout=5)

        registers = self._parse_registers(output)
        return {
            "status": "paused",
            "command": f"registers {args}".strip(),
            "message": f"Registers ({len(registers)}):\n{output}",
            "registers": registers,
            "current_location": self._get_frame_info(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # memory read
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

        self._send(f'memory read --size 1 --count {length} --format x {addr}')
        output = self._collect_output(timeout=10)

        hex_data = self._extract_hex_from_memory(output)
        return {
            "status": "paused",
            "command": f"memory {args}",
            "message": f"Memory at {addr} ({length} bytes):\n{output}",
            "memory": {
                "address": addr,
                "length": length,
                "hex": hex_data,
            },
            "current_location": self._get_frame_info(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # memory write
    # ------------------------------------------------------------------
    def cmd_memory_write(self, args: str) -> dict:
        parts = args.strip().split()
        if len(parts) < 2:
            return self._error("Usage: memory_write <address> <hex_bytes>\n"
                               "  e.g.: memory_write 0x401000 90909090")
        addr = parts[0]
        hex_bytes = ''.join(parts[1:]).replace(' ', '')
        try:
            raw = bytes.fromhex(hex_bytes)
        except ValueError:
            return self._error(f"Invalid hex bytes: {hex_bytes}")

        # LLDB memory write takes space-separated byte values
        byte_args = ' '.join(f'0x{b:02x}' for b in raw)
        self._send(f'memory write {addr} {byte_args}')
        output = self._collect_output(timeout=5)

        if 'error' in output.lower():
            return self._error(f"Memory write error: {output}")

        return {
            "status": "paused",
            "command": f"memory_write {args}",
            "message": f"Wrote {len(raw)} bytes at {addr}: {hex_bytes}",
            "bytes_written": len(raw),
            "current_location": self._get_frame_info(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # patch
    # ------------------------------------------------------------------
    def cmd_patch(self, args: str) -> dict:
        parts = args.strip().split()
        if len(parts) < 2:
            return self._error("Usage: patch <address> <hex_bytes>")
        addr = parts[0]
        hex_bytes = ''.join(parts[1:]).replace(' ', '')
        try:
            new_bytes = bytes.fromhex(hex_bytes)
        except ValueError:
            return self._error(f"Invalid hex bytes: {hex_bytes}")

        # Read original
        self._send(f'memory read --size 1 --count {len(new_bytes)} --format x {addr}')
        orig_output = self._collect_output(timeout=5)
        original_hex = self._extract_hex_from_memory(orig_output)

        # Write
        write_result = self.cmd_memory_write(args)
        if write_result.get('status') == 'error':
            return write_result

        dis = self._quick_disassemble(5, addr)
        msg = (f"Patched {len(new_bytes)} bytes at {addr}\n"
               f"  Before: {original_hex}\n"
               f"  After:  {hex_bytes}")
        if dis:
            msg += f"\n\nDisassembly after patch:\n{dis}"

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
            "current_location": self._get_frame_info(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # nop
    # ------------------------------------------------------------------
    def cmd_nop(self, args: str) -> dict:
        parts = args.strip().split()
        if not parts:
            return self._error("Usage: nop <address> [byte_count]")
        addr = parts[0]
        count = 1
        if len(parts) >= 2:
            try:
                count = int(parts[1])
            except ValueError:
                return self._error(f"Invalid count: {parts[1]}")
        count = min(count, 256)
        return self.cmd_patch(f"{addr} {'90' * count}")

    # ------------------------------------------------------------------
    # Override breakpoint to support *addr syntax
    # ------------------------------------------------------------------
    def cmd_set_breakpoint(self, args: str) -> dict:
        parts = args.strip().split(None, 1)
        if not parts:
            return self._error("Usage: b <line> | b <func> | b *<address>")

        location = parts[0]
        if location.startswith('*'):
            addr = location[1:]  # strip the *
            self._send(f'breakpoint set --address {addr}')
            output = self._collect_output(timeout=5)
            return {
                "status": "paused" if self.is_paused else "running",
                "command": f"set_breakpoint {args}",
                "message": output or f"Breakpoint set at address {addr}",
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            }

        return super().cmd_set_breakpoint(args)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _quick_disassemble(self, count: int = 5, addr: str = None) -> str:
        if addr:
            self._send(f'disassemble --start-address {addr} --count {count}')
        else:
            self._send(f'disassemble --pc --count {count}')
        return self._collect_output(timeout=5)

    def _parse_disassembly(self, output: str) -> list:
        """Parse LLDB disassembly output into structured data."""
        asm_data = []
        for line in output.split('\n'):
            line = line.strip()
            if not line or line.startswith('->'):
                # Current instruction indicator
                line = line.lstrip('-> ').strip()
            # Pattern: 0x00401000 <+0>:  movl   $0x0, -0x4(%rbp)
            m = re.match(r'(0x[0-9a-fA-F]+)\s*(?:<([^>]*)>)?:?\s+(.*)', line)
            if m:
                asm_data.append({
                    "address": m.group(1),
                    "offset": m.group(2) or "",
                    "instruction": m.group(3).strip(),
                })
        return asm_data

    def _parse_registers(self, output: str) -> dict:
        """Parse LLDB register read output."""
        registers = {}
        for line in output.split('\n'):
            line = line.strip()
            # Pattern: rax = 0x0000000000000001
            m = re.match(r'(\w+)\s*=\s*(0x[0-9a-fA-F]+)', line)
            if m:
                registers[m.group(1)] = m.group(2)
        return registers

    def _extract_hex_from_memory(self, output: str) -> str:
        """Extract hex bytes from LLDB memory read output."""
        hex_bytes = []
        for line in output.split('\n'):
            # Pattern: 0x7fff...: 0x48 0x89 0xe5 ...
            m = re.match(r'0x[0-9a-fA-F]+:\s+(.*)', line)
            if m:
                for token in m.group(1).split():
                    token = token.strip()
                    if token.startswith('0x'):
                        hex_bytes.append(token[2:])
                    elif re.match(r'^[0-9a-fA-F]{2}$', token):
                        hex_bytes.append(token)
        return ''.join(hex_bytes)
