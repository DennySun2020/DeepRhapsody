"""CDB (Windows) assembly-level debugging extension.

Subclasses CdbDebugger to add assembly/machine-code commands.
CDB commands: u (unassemble), r (registers), db/dd (display memory),
eb/ed (edit memory), t (trace = stepi), p (step over = nexti).
"""

import re
from typing import Dict, List, Optional

from debuggers.cpp_cdb import CdbDebugger


class CdbAsmDebugger(CdbDebugger):
    """CDB debugger extended with assembly / machine-code commands."""

    # ------------------------------------------------------------------
    # disassemble
    # ------------------------------------------------------------------
    def cmd_disassemble(self, args: str) -> dict:
        parts = args.strip().split()
        addr = "@$ip"  # CDB pseudo-register for instruction pointer
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

        # CDB: u <addr> L<count> — unassemble <count> instructions
        self._send(f'u {addr} L{count}')
        output = self._collect_output(timeout=10)

        asm_data = self._parse_disassembly(output)
        return {
            "status": "paused",
            "command": f"disassemble {args}",
            "message": f"Disassembly ({len(asm_data)} instructions):\n{output}",
            "disassembly": asm_data,
            "current_location": self._parse_location(output),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # stepi — CDB 't' is already instruction-level trace
    # ------------------------------------------------------------------
    def cmd_stepi(self) -> dict:
        self._send('t')
        output = self._collect_output(timeout=30)
        loc = self._parse_location(output)
        # Get disassembly context
        dis = self._quick_disassemble(3)
        msg = self._format_stop_message(output)
        if dis:
            msg += "\n" + dis
        return {
            "status": "paused",
            "command": "stepi",
            "message": msg,
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # nexti — CDB 'p' is step-over (instruction level)
    # ------------------------------------------------------------------
    def cmd_nexti(self) -> dict:
        self._send('p')
        output = self._collect_output(timeout=30)
        loc = self._parse_location(output)
        dis = self._quick_disassemble(3)
        msg = self._format_stop_message(output)
        if dis:
            msg += "\n" + dis
        return {
            "status": "paused",
            "command": "nexti",
            "message": msg,
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # registers
    # ------------------------------------------------------------------
    def cmd_registers(self, args: str = "") -> dict:
        self._send('r')
        output = self._collect_output(timeout=5)
        registers = self._parse_registers(output)
        return {
            "status": "paused",
            "command": f"registers {args}".strip(),
            "message": f"Registers ({len(registers)}):\n{output}",
            "registers": registers,
            "current_location": self._parse_location(""),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # memory read — CDB 'db' (display bytes)
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

        # CDB: db <addr> L<count> — display bytes
        self._send(f'db {addr} L{length}')
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
            "current_location": None,
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    # ------------------------------------------------------------------
    # memory write — CDB 'eb' (edit bytes)
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

        # CDB: eb <addr> <b1> <b2> ... — edit bytes
        byte_args = ' '.join(f'{b:02x}' for b in raw)
        self._send(f'eb {addr} {byte_args}')
        output = self._collect_output(timeout=5)

        if 'error' in output.lower() or '^' in output:
            return self._error(f"Memory write error: {output}")

        return {
            "status": "paused",
            "command": f"memory_write {args}",
            "message": f"Wrote {len(raw)} bytes at {addr}: {hex_bytes}",
            "bytes_written": len(raw),
            "current_location": None,
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
        self._send(f'db {addr} L{len(new_bytes)}')
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
            "current_location": None,
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
            addr = location[1:]
            self._send(f'bp {addr}')
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
    def _quick_disassemble(self, count: int = 5, addr: str = "@$ip") -> str:
        self._send(f'u {addr} L{count}')
        return self._collect_output(timeout=5)

    def _parse_disassembly(self, output: str) -> list:
        """Parse CDB unassemble output.

        CDB format:
            module!function+0x1a:
            00007ff6`12345678 4889e5         mov     rbp,rsp
        """
        asm_data = []
        for line in output.split('\n'):
            line = line.strip()
            # Pattern: addr hexbytes instruction
            m = re.match(
                r'([0-9a-fA-F`]+)\s+([0-9a-fA-F]+)\s+(.*)',
                line
            )
            if m:
                addr = m.group(1).replace('`', '')
                asm_data.append({
                    "address": f"0x{addr}",
                    "bytes": m.group(2),
                    "instruction": m.group(3).strip(),
                })
        return asm_data

    def _parse_registers(self, output: str) -> dict:
        """Parse CDB register output.

        CDB format:
            rax=00000000deadbeef rbx=0000000000000000 ...
        """
        registers = {}
        for token in re.findall(r'(\w+)=([0-9a-fA-F]+)', output):
            registers[token[0]] = f"0x{token[1]}"
        return registers

    def _extract_hex_from_memory(self, output: str) -> str:
        """Extract hex bytes from CDB db output.

        CDB db format:
            00007ff6`12340000  48 89 e5 48 83 ec 20 ...  H..H.. .
        """
        hex_bytes = []
        for line in output.split('\n'):
            # CDB db output: address  hex_bytes  ascii
            m = re.match(r'[0-9a-fA-F`]+\s+((?:[0-9a-fA-F]{2}\s*(?:-\s*)?)+)', line)
            if m:
                hex_part = m.group(1)
                for token in hex_part.split():
                    token = token.strip('-').strip()
                    if re.match(r'^[0-9a-fA-F]{2}$', token):
                        hex_bytes.append(token)
        return ''.join(hex_bytes)
