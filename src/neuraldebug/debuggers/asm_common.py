"""Assembly-level debug server and utilities.

Provides:
- ``AsmDebugServer`` — subclass of CppDebugServer with assembly command dispatch
- ``create_asm_debugger()`` — factory that creates the right Asm*Debugger
- ``BinaryPatcher`` — on-disk binary patching utility
"""

import os
import shutil
from typing import List, Optional, Tuple

from debug_common import BaseDebugServer
from debuggers.cpp_common import CppDebugServer, find_debugger
from debuggers.asm_gdb import GdbAsmDebugger
from debuggers.asm_lldb import LldbAsmDebugger
from debuggers.asm_cdb import CdbAsmDebugger


# ======================================================================
#  Factory
# ======================================================================

def create_asm_debugger(executable: str, debugger_type: str, debugger_path: str,
                        source_paths: Optional[List[str]] = None,
                        attach_pid: Optional[int] = None,
                        core_dump: Optional[str] = None,
                        program_args: Optional[str] = None):
    """Create an assembly-capable debugger (GDB/LLDB/CDB)."""
    kwargs = dict(
        source_paths=source_paths,
        attach_pid=attach_pid,
        core_dump=core_dump,
        program_args=program_args,
    )
    if debugger_type == 'gdb':
        return GdbAsmDebugger(executable, debugger_path, **kwargs)
    elif debugger_type == 'lldb':
        return LldbAsmDebugger(executable, debugger_path, **kwargs)
    elif debugger_type == 'cdb':
        return CdbAsmDebugger(executable, debugger_path, **kwargs)
    else:
        raise ValueError(f"Unknown debugger type: {debugger_type}")


# ======================================================================
#  AsmDebugServer
# ======================================================================

class AsmDebugServer(CppDebugServer):
    """C/C++ debug server extended with assembly / machine-code commands."""

    LANGUAGE = "C/C++ (Assembly)"
    SCRIPT_NAME = "asm_debug_session.py"

    def _dispatch_extra(self, action: str, args: str):
        # Assembly commands
        if action in ("disassemble", "dis", "disas"):
            return self.debugger.cmd_disassemble(args)
        elif action in ("stepi", "si_asm"):
            return self.debugger.cmd_stepi()
        elif action in ("nexti", "ni_asm"):
            return self.debugger.cmd_nexti()
        elif action in ("registers", "reg", "regs"):
            return self.debugger.cmd_registers(args)
        elif action in ("memory", "mem"):
            return self.debugger.cmd_memory_read(args)
        elif action in ("memory_write", "mw"):
            return self.debugger.cmd_memory_write(args)
        elif action in ("patch",):
            return self.debugger.cmd_patch(args)
        elif action in ("nop",):
            return self.debugger.cmd_nop(args)
        elif action in ("patch_file",):
            return self._cmd_patch_file(args)
        return None

    def _available_commands(self) -> List[str]:
        cmds = super()._available_commands()
        cmds.extend([
            "disassemble", "stepi", "nexti", "registers",
            "memory", "memory_write", "patch", "nop", "patch_file",
        ])
        return cmds

    # ------------------------------------------------------------------
    # patch_file — on-disk binary patching (no debugger needed)
    # ------------------------------------------------------------------
    def _cmd_patch_file(self, args: str) -> dict:
        """Patch a binary file on disk.

        Usage: patch_file <filepath> <offset> <hex_bytes>
            offset is a decimal or hex (0x...) file offset
        """
        parts = args.strip().split()
        if len(parts) < 3:
            return {"status": "error", "command": "patch_file",
                    "message": ("Usage: patch_file <filepath> <offset> <hex_bytes>\n"
                                "  e.g.: patch_file program.exe 0x1a40 9090"),
                    "current_location": None, "call_stack": [],
                    "local_variables": {}, "stdout_new": "", "stderr_new": ""}

        filepath = parts[0]
        offset_str = parts[1]
        hex_bytes = ''.join(parts[2:]).replace(' ', '')

        try:
            offset = int(offset_str, 16) if offset_str.startswith('0x') else int(offset_str)
        except ValueError:
            return {"status": "error", "command": "patch_file",
                    "message": f"Invalid offset: {offset_str}",
                    "current_location": None, "call_stack": [],
                    "local_variables": {}, "stdout_new": "", "stderr_new": ""}

        try:
            result = BinaryPatcher.patch_file(filepath, offset, hex_bytes)
        except Exception as e:
            return {"status": "error", "command": "patch_file",
                    "message": str(e),
                    "current_location": None, "call_stack": [],
                    "local_variables": {}, "stdout_new": "", "stderr_new": ""}

        return {
            "status": "ok",
            "command": "patch_file",
            "message": result["message"],
            "patch": result,
            "current_location": None,
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }


# ======================================================================
#  BinaryPatcher — on-disk modification
# ======================================================================

class BinaryPatcher:
    """Utility for patching binary files on disk."""

    @staticmethod
    def patch_file(filepath: str, offset: int, hex_bytes: str,
                   backup: bool = True) -> dict:
        """Patch bytes at a given file offset.

        Args:
            filepath: Path to the binary file.
            offset: Byte offset from the start of the file.
            hex_bytes: Hex string of new bytes (e.g. '90909090').
            backup: If True, create a .bak backup before patching.

        Returns:
            Dict with patch details (original, new, backup path).
        """
        try:
            new_bytes = bytes.fromhex(hex_bytes)
        except ValueError:
            raise ValueError(f"Invalid hex bytes: {hex_bytes}")

        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        file_size = os.path.getsize(filepath)
        if offset < 0 or offset + len(new_bytes) > file_size:
            raise ValueError(
                f"Patch range [{offset:#x}..{offset + len(new_bytes):#x}) "
                f"exceeds file size ({file_size:#x})"
            )

        # Read original bytes
        with open(filepath, 'rb') as f:
            f.seek(offset)
            original_bytes = f.read(len(new_bytes))

        original_hex = original_bytes.hex()

        # Create backup
        backup_path = None
        if backup:
            backup_path = filepath + '.bak'
            if not os.path.exists(backup_path):
                shutil.copy2(filepath, backup_path)

        # Write new bytes
        with open(filepath, 'r+b') as f:
            f.seek(offset)
            f.write(new_bytes)

        msg = (f"Patched {len(new_bytes)} bytes in {os.path.basename(filepath)} "
               f"at offset {offset:#x}\n"
               f"  Before: {original_hex}\n"
               f"  After:  {hex_bytes}")
        if backup_path:
            msg += f"\n  Backup: {backup_path}"

        return {
            "filepath": filepath,
            "offset": offset,
            "original": original_hex,
            "new": hex_bytes,
            "size": len(new_bytes),
            "backup": backup_path,
            "message": msg,
        }

    @staticmethod
    def nop_file(filepath: str, offset: int, count: int,
                 backup: bool = True) -> dict:
        """NOP out bytes in a binary file on disk."""
        return BinaryPatcher.patch_file(
            filepath, offset, '90' * count, backup=backup
        )
