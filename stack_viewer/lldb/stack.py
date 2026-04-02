from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import lldb


def _format_ascii(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in data)


@dataclass
class ByteBlock:
    address: int
    data: bytes = b""
    error: Optional[str] = None

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def end(self) -> int:
        return self.address + self.size

    @property
    def byte_values(self) -> list[int]:
        return list(self.data)

    @property
    def hex_bytes(self) -> str:
        return " ".join(f"{byte:02x}" for byte in self.data)

    @property
    def ascii_preview(self) -> str:
        return _format_ascii(self.data)

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "end": self.end,
            "size": self.size,
            "data": self.data,
            "byte_values": self.byte_values,
            "hex_bytes": self.hex_bytes,
            "ascii_preview": self.ascii_preview,
            "error": self.error,
        }


@dataclass
class StackEntry:
    index: int
    stack_address: int
    slot_bytes: ByteBlock
    value: Optional[int]
    symbol: Optional[str] = None
    string: Optional[str] = None
    pointee_region_id: Optional[str] = None
    pointee_region_offset: Optional[int] = None
    stack_region_id: Optional[str] = None
    stack_region_offset: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "stack_address": self.stack_address,
            "slot_bytes": self.slot_bytes.to_dict(),
            "value": self.value,
            "symbol": self.symbol,
            "string": self.string,
            "pointee_region_id": self.pointee_region_id,
            "pointee_region_offset": self.pointee_region_offset,
            "stack_region_id": self.stack_region_id,
            "stack_region_offset": self.stack_region_offset,
            "error": self.error,
        }


@dataclass
class StackMemoryRegion:
    region_id: str
    start: int
    end: int
    bytes_block: ByteBlock
    referenced_stack_addresses: list[int] = field(default_factory=list)
    referenced_values: list[int] = field(default_factory=list)
    referenced_entry_indexes: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.end - self.start

    def contains(self, address: int) -> bool:
        return self.start <= address < self.end

    def to_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "start": self.start,
            "end": self.end,
            "size": self.size,
            "bytes_block": self.bytes_block.to_dict(),
            "referenced_stack_addresses": list(self.referenced_stack_addresses),
            "referenced_values": list(self.referenced_values),
            "referenced_entry_indexes": list(self.referenced_entry_indexes),
        }


@dataclass
class StackRegion:
    region_id: str
    kind: str
    label: str
    start: int
    end: int
    bytes_block: ByteBlock
    entry_indexes: list[int] = field(default_factory=list)
    stack_addresses: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.end - self.start

    def contains(self, address: int) -> bool:
        return self.start <= address < self.end

    def to_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "kind": self.kind,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "size": self.size,
            "bytes_block": self.bytes_block.to_dict(),
            "entry_indexes": list(self.entry_indexes),
            "stack_addresses": list(self.stack_addresses),
        }


@dataclass
class StackSnapshot:
    stack_pointer: Optional[int]
    pointer_size: int
    frame_pc: Optional[int]
    frame_sp: Optional[int]
    function_name: Optional[str]
    view_direction: str = "up"
    entries: list[StackEntry] = field(default_factory=list)
    stack_regions: list[StackRegion] = field(default_factory=list)
    memory_regions: list[StackMemoryRegion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stack_pointer": self.stack_pointer,
            "pointer_size": self.pointer_size,
            "frame_pc": self.frame_pc,
            "frame_sp": self.frame_sp,
            "function_name": self.function_name,
            "view_direction": self.view_direction,
            "entries": [entry.to_dict() for entry in self.entries],
            "stack_regions": [region.to_dict() for region in self.stack_regions],
            "memory_regions": [region.to_dict() for region in self.memory_regions],
            "errors": list(self.errors),
        }


class StackContext:
    def __init__(self, debugger):
        self.debugger = debugger
        self.target = None
        self.process = None
        self.thread = None
        self.frame = None
        self.pointer_size = 8
        self.rsp = None
        self.entries: list[StackEntry] = []
        self.stack_regions: list[StackRegion] = []
        self.memory_regions: list[StackMemoryRegion] = []
        self.errors: list[str] = []
        self.snapshot = StackSnapshot(
            stack_pointer=None,
            pointer_size=self.pointer_size,
            frame_pc=None,
            frame_sp=None,
            function_name=None,
            view_direction="up",
        )
        self.sync()

    def _make_error(self):
        return lldb.SBError()

    def _error_message(self, error: Optional[object]) -> Optional[str]:
        if error is None:
            return None
        if error.Success():
            return None
        if hasattr(error, "GetCString"):
            message = error.GetCString()
            if message:
                return message
        return str(error)

    def sync(self) -> StackSnapshot:
        self.target = self.debugger.GetSelectedTarget()
        self.process = self.target.GetProcess() if self.target and self.target.IsValid() else None
        self.thread = self.process.GetSelectedThread() if self.process and self.process.IsValid() else None
        self.frame = self.thread.GetSelectedFrame() if self.thread and self.thread.IsValid() else None

        pointer_size = 8
        if self.process and self.process.IsValid():
            pointer_size = self.process.GetAddressByteSize() or pointer_size

        self.pointer_size = pointer_size
        self.rsp = self._read_stack_pointer()

        self.snapshot = StackSnapshot(
            stack_pointer=self.rsp,
            pointer_size=self.pointer_size,
            frame_pc=self.frame.GetPC() if self.frame and self.frame.IsValid() else None,
            frame_sp=self.frame.GetSP() if self.frame and self.frame.IsValid() else None,
            function_name=self.frame.GetFunctionName() if self.frame and self.frame.IsValid() else None,
            view_direction="up",
        )
        self.entries = self.snapshot.entries
        self.stack_regions = self.snapshot.stack_regions
        self.memory_regions = self.snapshot.memory_regions
        self.errors = self.snapshot.errors
        return self.snapshot

    def _read_stack_pointer(self) -> Optional[int]:
        if not self.frame or not self.frame.IsValid():
            return None

        for register_name in ("rsp", "sp"):
            register = self.frame.FindRegister(register_name)
            if register and register.IsValid():
                return register.GetValueAsUnsigned()

        return self.frame.GetSP()

    def read_memory(self, addr: int, size: int) -> ByteBlock:
        if not self.process or not self.process.IsValid():
            return ByteBlock(address=addr, error="No valid LLDB process selected")

        error = self._make_error()
        data = self.process.ReadMemory(addr, size, error)
        message = self._error_message(error)

        if message:
            return ByteBlock(address=addr, error=message)

        return ByteBlock(address=addr, data=data or b"")

    def read_u64(self, addr: int) -> Optional[int]:
        block = self.read_memory(addr, self.pointer_size)
        if block.error or len(block.data) != self.pointer_size:
            return None
        return int.from_bytes(block.data, "little")

    def read_string(self, addr: int, max_len: int = 100) -> Optional[str]:
        if not self.process or not self.process.IsValid():
            return None

        error = self._make_error()
        try:
            value = self.process.ReadCStringFromMemory(addr, max_len, error)
        except Exception:
            return None

        if self._error_message(error) or not value:
            return None

        return value

    def get_symbol(self, addr: int) -> Optional[str]:
        if not self.target or not self.target.IsValid():
            return None

        addr_obj = self.target.ResolveLoadAddress(addr)
        symbol = addr_obj.GetSymbol()
        if symbol and symbol.IsValid():
            return symbol.GetName()
        return None

    def _collect_region_requests(
        self,
        entries: list[StackEntry],
        deref_bytes: int,
    ) -> list[tuple[int, int]]:
        requests: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()

        for entry in entries:
            if entry.value in (None, 0):
                continue

            request = (entry.value, entry.value + deref_bytes)
            if request in seen:
                continue

            seen.add(request)
            requests.append(request)

        requests.sort(key=lambda item: item[0])
        return requests

    def _merge_region_requests(self, requests: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if not requests:
            return []

        merged: list[list[int]] = [[requests[0][0], requests[0][1]]]

        for start, end in requests[1:]:
            current = merged[-1]
            if start <= current[1]:
                current[1] = max(current[1], end)
                continue

            merged.append([start, end])

        return [(start, end) for start, end in merged]

    def _build_memory_regions(
        self,
        entries: list[StackEntry],
        deref_bytes: int,
    ) -> list[StackMemoryRegion]:
        requests = self._collect_region_requests(entries, deref_bytes)
        merged = self._merge_region_requests(requests)
        regions: list[StackMemoryRegion] = []

        for start, end in merged:
            block = self.read_memory(start, end - start)
            if block.error:
                continue

            region = StackMemoryRegion(
                region_id=f"{start:#x}:{end:#x}",
                start=start,
                end=end,
                bytes_block=block,
            )
            regions.append(region)

        for entry in entries:
            if entry.value is None:
                continue

            for region in regions:
                if not region.contains(entry.value):
                    continue

                entry.pointee_region_id = region.region_id
                entry.pointee_region_offset = entry.value - region.start
                region.referenced_stack_addresses.append(entry.stack_address)
                region.referenced_values.append(entry.value)
                region.referenced_entry_indexes.append(entry.index)
                break

        return regions

    def _entry_address_bounds(self, snapshot: StackSnapshot) -> tuple[int, int]:
        if snapshot.entries:
            start = min(entry.stack_address for entry in snapshot.entries)
            end = max(entry.stack_address for entry in snapshot.entries) + self.pointer_size
            return start, end

        base = snapshot.stack_pointer or 0
        return base, base + self.pointer_size

    def _looks_like_stack_pointer(self, snapshot: StackSnapshot, entry: StackEntry) -> bool:
        if entry.value is None or snapshot.stack_pointer is None:
            return False
        if entry.symbol:
            return False

        entry_start, entry_end = self._entry_address_bounds(snapshot)
        stack_start = entry_start - (self.pointer_size * 2)
        stack_end = entry_end + (self.pointer_size * 2)
        return stack_start <= entry.value <= stack_end and entry.value % self.pointer_size == 0

    def _looks_like_data_region(self, entry: StackEntry) -> bool:
        data = entry.slot_bytes.data
        if not data:
            return False

        printable_count = sum(32 <= byte <= 126 for byte in data)
        repeated_non_zero = len(set(data)) <= 2 and any(byte != 0 for byte in data)
        return printable_count >= max(4, len(data) // 2) or repeated_non_zero

    def _stack_region_signature(self, snapshot: StackSnapshot, entry: StackEntry) -> tuple[str, str]:
        if entry.symbol:
            return ("return-address", "return address")
        if self._looks_like_stack_pointer(snapshot, entry):
            return ("saved-rbp", "saved rbp")
        if self._looks_like_data_region(entry):
            return ("stack-data", "stack bytes")
        if entry.pointee_region_id:
            return ("stack-pointer", "pointer")
        if entry.value in (None, 0):
            return ("stack-zero", "zero / padding")
        return ("stack-value", "stack value")

    def _build_stack_regions(self, snapshot: StackSnapshot) -> list[StackRegion]:
        if not snapshot.entries:
            return []

        regions: list[StackRegion] = []
        current_entries: list[StackEntry] = []
        current_kind = ""
        current_label = ""

        def flush_region() -> None:
            if not current_entries:
                return

            start = current_entries[0].stack_address
            end = current_entries[-1].stack_address + self.pointer_size
            region = StackRegion(
                region_id=f"{start:#x}:{end:#x}",
                kind=current_kind,
                label=current_label,
                start=start,
                end=end,
                bytes_block=ByteBlock(
                    address=start,
                    data=b"".join(
                        entry.slot_bytes.data.ljust(self.pointer_size, b"\x00")
                        for entry in current_entries
                    ),
                ),
                entry_indexes=[entry.index for entry in current_entries],
                stack_addresses=[entry.stack_address for entry in current_entries],
            )
            for entry in current_entries:
                entry.stack_region_id = region.region_id
                entry.stack_region_offset = entry.stack_address - region.start
            regions.append(region)

        for entry in snapshot.entries:
            kind, label = self._stack_region_signature(snapshot, entry)
            if not current_entries:
                current_entries = [entry]
                current_kind = kind
                current_label = label
                continue

            if kind == current_kind:
                current_entries.append(entry)
                continue

            flush_region()
            current_entries = [entry]
            current_kind = kind
            current_label = label

        flush_region()
        return regions

    def collect_stack(
        self,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> StackSnapshot:
        snapshot = self.sync()
        snapshot.errors.clear()
        snapshot.entries.clear()
        snapshot.stack_regions.clear()
        snapshot.memory_regions.clear()
        if direction == "center":
            snapshot.view_direction = "center"
        elif direction == "down":
            snapshot.view_direction = "down"
        else:
            snapshot.view_direction = "up"

        if snapshot.stack_pointer is None:
            snapshot.errors.append("Unable to resolve the current stack pointer")
            return snapshot

        count = max(1, count)
        if snapshot.view_direction == "center":
            below_count = count // 2
            base_address = snapshot.stack_pointer - (below_count * self.pointer_size)
        elif snapshot.view_direction == "down":
            base_address = snapshot.stack_pointer - (count * self.pointer_size)
        else:
            base_address = snapshot.stack_pointer

        for index in range(count):
            stack_address = base_address + (index * self.pointer_size)
            slot_bytes = self.read_memory(stack_address, self.pointer_size)

            value = None
            error_message = slot_bytes.error
            if not slot_bytes.error and len(slot_bytes.data) == self.pointer_size:
                value = int.from_bytes(slot_bytes.data, "little")
            elif not error_message:
                error_message = "Incomplete stack slot bytes"

            entry = StackEntry(
                index=index,
                stack_address=stack_address,
                slot_bytes=slot_bytes,
                value=value,
                symbol=self.get_symbol(value) if value is not None else None,
                string=self.read_string(value) if value not in (None, 0) else None,
                error=error_message,
            )
            snapshot.entries.append(entry)

        snapshot.stack_regions = self._build_stack_regions(snapshot)
        snapshot.memory_regions = self._build_memory_regions(snapshot.entries, deref_bytes)
        self.entries = snapshot.entries
        self.stack_regions = snapshot.stack_regions
        self.memory_regions = snapshot.memory_regions
        self.errors = snapshot.errors
        return snapshot

    def get_snapshot(
        self,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> StackSnapshot:
        return self.collect_stack(count=count, deref_bytes=deref_bytes, direction=direction)

    def get_stack_entries(
        self,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> list[StackEntry]:
        return self.collect_stack(count=count, deref_bytes=deref_bytes, direction=direction).entries

    def get_memory_regions(
        self,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> list[StackMemoryRegion]:
        return self.collect_stack(count=count, deref_bytes=deref_bytes, direction=direction).memory_regions

    def get_stack_regions(
        self,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> list[StackRegion]:
        return self.collect_stack(count=count, deref_bytes=deref_bytes, direction=direction).stack_regions

    def dump(
        self,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> StackSnapshot:
        return self.collect_stack(count=count, deref_bytes=deref_bytes, direction=direction)
