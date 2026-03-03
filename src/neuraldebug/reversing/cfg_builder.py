"""Control flow graph builder.

Constructs a CFG from decoded instructions by splitting code into
basic blocks at branch/jump/call boundaries.  Outputs in multiple
formats: structured dict, ASCII art, Mermaid diagram.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .x86_decoder import X86Decoder, Instruction, InsnType


@dataclass
class BasicBlock:
    address: int
    size: int = 0
    instructions: List[Instruction] = field(default_factory=list)
    successors: List[int] = field(default_factory=list)
    predecessors: List[int] = field(default_factory=list)
    is_entry: bool = False
    is_exit: bool = False     # ends with RET

    @property
    def end_address(self) -> int:
        return self.address + self.size

    @property
    def last_insn(self) -> Optional[Instruction]:
        return self.instructions[-1] if self.instructions else None

    @property
    def terminator_type(self) -> str:
        li = self.last_insn
        if not li:
            return "unknown"
        if li.is_ret:
            return "return"
        if li.insn_type == InsnType.JMP:
            return "unconditional_jump"
        if li.insn_type == InsnType.JCC:
            return "conditional_jump"
        if li.insn_type == InsnType.CALL:
            return "call"
        return "fallthrough"


@dataclass
class CFG:
    """Control flow graph for a single function."""
    function_addr: int
    function_name: str = ""
    blocks: Dict[int, BasicBlock] = field(default_factory=dict)
    edges: List[Tuple[int, int, str]] = field(default_factory=list)

    @property
    def num_blocks(self) -> int:
        return len(self.blocks)

    @property
    def num_edges(self) -> int:
        return len(self.edges)

    def get_entry_block(self) -> Optional[BasicBlock]:
        return self.blocks.get(self.function_addr)


class CFGBuilder:
    """Build control flow graphs from decoded instructions."""

    def __init__(self, bits: int = 64):
        self.bits = bits
        self.decoder = X86Decoder(bits)

    def build(self, data: bytes, func_addr: int, func_size: int,
              data_offset: int = 0, func_name: str = "") -> CFG:
        """Build a CFG for a function.

        Args:
            data: Raw binary data (entire file or section).
            func_addr: Virtual address of function start.
            func_size: Estimated function size in bytes.
            data_offset: File offset corresponding to func_addr.
            func_name: Optional function name.
        """
        cfg = CFG(function_addr=func_addr, function_name=func_name)

        # Decode all instructions in the function
        section = data[data_offset:data_offset + func_size]
        if not section:
            return cfg
        insns = self.decoder.decode_range(section, 0, len(section), func_addr)
        if not insns:
            return cfg

        # Step 1: Find block leaders (addresses that start basic blocks)
        leaders: Set[int] = {func_addr}
        func_end = func_addr + func_size

        for insn in insns:
            next_addr = insn.address + insn.size
            if insn.insn_type == InsnType.JCC:
                # Conditional jump: two successors
                if insn.target is not None:
                    leaders.add(insn.target)
                leaders.add(next_addr)
            elif insn.insn_type == InsnType.JMP:
                if insn.target is not None:
                    leaders.add(insn.target)
                leaders.add(next_addr)
            elif insn.is_ret:
                leaders.add(next_addr)

        # Filter leaders within function bounds
        leaders = {a for a in leaders if func_addr <= a < func_end}

        # Step 2: Build basic blocks
        sorted_leaders = sorted(leaders)
        insn_map = {insn.address: insn for insn in insns}
        all_addrs = sorted(insn_map.keys())

        for i, leader_addr in enumerate(sorted_leaders):
            block = BasicBlock(address=leader_addr, is_entry=(leader_addr == func_addr))
            # Collect instructions until next leader or end
            next_leader = sorted_leaders[i + 1] if i + 1 < len(sorted_leaders) else func_end

            for addr in all_addrs:
                if addr < leader_addr:
                    continue
                if addr >= next_leader:
                    break
                insn = insn_map[addr]
                block.instructions.append(insn)
                # Stop block at control flow instructions
                if insn.is_ret or insn.insn_type in (InsnType.JMP, InsnType.JCC):
                    break

            if block.instructions:
                block.size = sum(ins.size for ins in block.instructions)
                last = block.instructions[-1]

                if last.is_ret:
                    block.is_exit = True
                elif last.insn_type == InsnType.JCC:
                    if last.target and func_addr <= last.target < func_end:
                        block.successors.append(last.target)
                    fallthrough = last.address + last.size
                    if func_addr <= fallthrough < func_end:
                        block.successors.append(fallthrough)
                elif last.insn_type == InsnType.JMP:
                    if last.target and func_addr <= last.target < func_end:
                        block.successors.append(last.target)
                else:
                    # Fallthrough
                    fallthrough = last.address + last.size
                    if func_addr <= fallthrough < func_end:
                        block.successors.append(fallthrough)

                cfg.blocks[leader_addr] = block

        # Step 3: Build edges and predecessor links
        for addr, block in cfg.blocks.items():
            for succ_addr in block.successors:
                label = "true" if block.terminator_type == "conditional_jump" else ""
                cfg.edges.append((addr, succ_addr, label))
                if succ_addr in cfg.blocks:
                    cfg.blocks[succ_addr].predecessors.append(addr)

        return cfg

    # ---- Output formats ---------------------------------------------------

    @staticmethod
    def to_ascii(cfg: CFG, max_insns_per_block: int = 8) -> str:
        """Render CFG as ASCII art."""
        if not cfg.blocks:
            return "(empty CFG)"

        lines = []
        name = cfg.function_name or f"sub_{cfg.function_addr:x}"
        lines.append(f"=== CFG: {name} ({cfg.num_blocks} blocks, {cfg.num_edges} edges) ===")
        lines.append("")

        for addr in sorted(cfg.blocks.keys()):
            block = cfg.blocks[addr]
            # Header
            markers = []
            if block.is_entry:
                markers.append("ENTRY")
            if block.is_exit:
                markers.append("EXIT")
            marker_str = f" [{', '.join(markers)}]" if markers else ""
            lines.append(f"┌─ BB_{addr:x}{marker_str} ({len(block.instructions)} insns, {block.size} bytes)")

            # Instructions
            shown = block.instructions[:max_insns_per_block]
            for insn in shown:
                hex_str = insn.hex_bytes.ljust(16)
                mn = insn.mnemonic or "???"
                target_str = ""
                if insn.target is not None:
                    target_str = f"  → 0x{insn.target:x}"
                lines.append(f"│  0x{insn.address:x}  {hex_str}  {mn}{target_str}")
            if len(block.instructions) > max_insns_per_block:
                lines.append(f"│  ... +{len(block.instructions) - max_insns_per_block} more")

            # Edges
            term = block.terminator_type
            if block.successors:
                targets = ", ".join(f"BB_{s:x}" for s in block.successors)
                lines.append(f"└─→ {targets}  ({term})")
            elif block.is_exit:
                lines.append(f"└─→ (return)")
            else:
                lines.append(f"└─→ (end)")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def to_mermaid(cfg: CFG) -> str:
        """Render CFG as a Mermaid flowchart."""
        if not cfg.blocks:
            return "graph TD\n  empty[Empty CFG]"

        lines = ["graph TD"]
        name = cfg.function_name or f"sub_{cfg.function_addr:x}"
        lines.append(f"  subgraph {name}")

        for addr in sorted(cfg.blocks.keys()):
            block = cfg.blocks[addr]
            n_insns = len(block.instructions)
            label = f"BB_{addr:x}\\n{n_insns} insns, {block.size}B"
            if block.is_entry:
                label = f"ENTRY\\n{label}"
            if block.is_exit:
                label = f"{label}\\nRET"
            node_id = f"BB_{addr:x}"
            lines.append(f"    {node_id}[\"{label}\"]")

        for src, dst, label in cfg.edges:
            src_id = f"BB_{src:x}"
            dst_id = f"BB_{dst:x}"
            if label:
                lines.append(f"    {src_id} -->|{label}| {dst_id}")
            else:
                lines.append(f"    {src_id} --> {dst_id}")

        lines.append("  end")
        return "\n".join(lines)

    @staticmethod
    def to_dict(cfg: CFG) -> dict:
        """Convert CFG to a JSON-serializable dict."""
        blocks = {}
        for addr, block in cfg.blocks.items():
            blocks[f"0x{addr:x}"] = {
                "address": f"0x{addr:x}",
                "size": block.size,
                "num_instructions": len(block.instructions),
                "is_entry": block.is_entry,
                "is_exit": block.is_exit,
                "terminator": block.terminator_type,
                "successors": [f"0x{s:x}" for s in block.successors],
                "predecessors": [f"0x{p:x}" for p in block.predecessors],
            }
        return {
            "function": cfg.function_name or f"sub_{cfg.function_addr:x}",
            "address": f"0x{cfg.function_addr:x}",
            "num_blocks": cfg.num_blocks,
            "num_edges": cfg.num_edges,
            "blocks": blocks,
            "edges": [
                {"from": f"0x{s:x}", "to": f"0x{d:x}", "label": l}
                for s, d, l in cfg.edges
            ],
        }
