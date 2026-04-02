from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
from typing import Optional


BOX_VERTICAL = "│"
BOX_HORIZONTAL = "─"
BOX_TOP_LEFT = "┌"
BOX_TOP_RIGHT = "┐"
BOX_BOTTOM_LEFT = "└"
BOX_BOTTOM_RIGHT = "┘"

ADDRESS_COLORS = [81, 214, 141, 45, 207, 220, 111, 177, 39, 149]
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
SEMANTIC_REGION_COLORS = {
    "return-address": 81,
    "saved-rbp": 177,
    "stack-data": 208,
    "stack-pointer": 45,
    "stack-zero": 244,
    "stack-value": 220,
}
SP_ADDRESS_COLOR = 203


def _hex(value: Optional[int]) -> str:
    return "-" if value is None else f"{value:#x}"


def _colorize(text: str, color_code: int, bold: bool = True) -> str:
    prefix = f"\033[1;38;5;{color_code}m" if bold else f"\033[38;5;{color_code}m"
    return f"{prefix}{text}\033[0m"


def _address_color(index: int) -> int:
    return ADDRESS_COLORS[index % len(ADDRESS_COLORS)]


def _semantic_color(region_kind: Optional[str], label: Optional[str], fallback_index: int) -> int:
    if region_kind in SEMANTIC_REGION_COLORS:
        return SEMANTIC_REGION_COLORS[region_kind]

    if label:
        return ADDRESS_COLORS[sum(ord(ch) for ch in label) % len(ADDRESS_COLORS)]

    return _address_color(fallback_index)


@dataclass
class StoredPaneFiles:
    root: Path
    stack: Path


@dataclass
class RenderMetrics:
    width: int
    height: int
    compact: bool


@dataclass
class TmuxLayout:
    session_name: str
    window_name: str
    stack_pane: str


@dataclass
class StackVisualRegion:
    address_lines: list[str]
    byte_lines: list[str]
    info_lines: list[str]
    color_code: int


class SnapshotFileStore:
    def __init__(self, root: Optional[Path] = None, namespace: str = "default"):
        base_root = root or Path("/tmp/dbgStackViewer")
        safe_namespace = namespace.replace("/", "_")
        self.root = base_root / safe_namespace
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = StoredPaneFiles(
            root=self.root,
            stack=self.root / "stack.txt",
        )

    def write_snapshot(self, snapshot, metrics: RenderMetrics) -> StoredPaneFiles:
        self.files.stack.write_text(self.render_snapshot(snapshot, metrics), encoding="utf-8")
        return self.files

    def render_snapshot(self, snapshot, metrics: RenderMetrics) -> str:
        return self._build_stack(snapshot, metrics)

    def measure_snapshot_height(self, snapshot, metrics: RenderMetrics) -> int:
        rendered = self.render_snapshot(snapshot, metrics)
        return len(rendered.rstrip("\n").splitlines())

    def _build_stack(self, snapshot, metrics: RenderMetrics) -> str:
        title = (
            "== STACK DETAIL VIEW =="
            if getattr(snapshot, "view_direction", "up") in {"down", "center"}
            else "== STACK MEMORY VIEW =="
        )
        lines = [title]

        if not snapshot.entries:
            lines.append("no stack entries")
            return "\n".join(lines) + "\n"

        range_start = snapshot.entries[0].stack_address
        range_end = snapshot.entries[-1].stack_address + snapshot.pointer_size
        if getattr(snapshot, "view_direction", "up") == "center":
            direction_label = "around-sp"
        elif getattr(snapshot, "view_direction", "up") == "down":
            direction_label = "below-sp"
        else:
            direction_label = "from-sp"
        summary = (
            f"sp={_hex(snapshot.stack_pointer)} "
            f"range={_hex(range_start)}..{_hex(range_end)} "
            f"view={direction_label} "
            f"fn={snapshot.function_name or '-'}"
        )
        lines.append(self._truncate(summary, max(24, metrics.width - 4)))
        if snapshot.errors:
            lines.append(self._truncate("errors: " + ", ".join(snapshot.errors), max(24, metrics.width - 4)))
        lines.append("")
        lines.extend(self._build_stack_visual(snapshot, metrics))
        lines = self._center_block(lines, metrics)

        return "\n".join(lines) + "\n"

    def _build_stack_visual(self, snapshot, metrics: RenderMetrics) -> list[str]:
        stack_regions = list(getattr(snapshot, "stack_regions", []))
        if not stack_regions:
            return ["no stack regions"]

        entries_by_index = {entry.index: entry for entry in snapshot.entries}
        regions = [
            self._make_stack_visual_region(snapshot, region, entries_by_index, region_index, metrics)
            for region_index, region in enumerate(stack_regions)
        ]

        address_width = min(
            max(self._visual_width(line) for region in regions for line in region.address_lines),
            18 if metrics.compact else 24,
        )
        byte_inner_width = max(
            [self._visual_width(line) for region in regions for line in region.byte_lines] or [1]
        )
        info_inner_width = max(
            [self._visual_width(line) for region in regions for line in region.info_lines] or [1]
        )

        total_budget = max(metrics.width - 8, 40)
        info_inner_width = min(info_inner_width, max(18, total_budget - address_width - byte_inner_width - 12))
        byte_width = byte_inner_width + 4
        info_width = info_inner_width + 4

        content_width = address_width + byte_width + info_width
        lines = [BOX_TOP_LEFT + (BOX_HORIZONTAL * content_width) + BOX_TOP_RIGHT]

        for region_index, region in enumerate(regions):
            byte_box_lines = self._build_box_lines(region.byte_lines, byte_inner_width, region.color_code)
            info_box_lines = self._build_box_lines(
                region.info_lines,
                info_inner_width,
                region.color_code,
                align="center",
            )
            address_lines = self._build_address_lines(region.address_lines, len(byte_box_lines))
            total_lines = max(len(address_lines), len(byte_box_lines), len(info_box_lines), 1)
            for line_index in range(total_lines):
                address = address_lines[line_index] if line_index < len(address_lines) else ""
                byte_box = byte_box_lines[line_index] if line_index < len(byte_box_lines) else (" " * byte_width)
                info_box = info_box_lines[line_index] if line_index < len(info_box_lines) else (" " * info_width)
                content = (
                    f"{self._pad_visual(address, address_width)}"
                    f"{self._pad_visual(byte_box, byte_width)}"
                    f"{self._pad_visual(info_box, info_width)}"
                )
                lines.append(f"{BOX_VERTICAL}{content}{BOX_VERTICAL}")

        lines.append(BOX_BOTTOM_LEFT + (BOX_HORIZONTAL * content_width) + BOX_BOTTOM_RIGHT)
        return lines

    def _make_stack_visual_region(
        self,
        snapshot,
        stack_region,
        entries_by_index,
        region_index: int,
        metrics: RenderMetrics,
    ) -> StackVisualRegion:
        region_entries = [
            entries_by_index[index]
            for index in stack_region.entry_indexes
            if index in entries_by_index
        ]
        color = _semantic_color(
            getattr(stack_region, "kind", None),
            getattr(stack_region, "label", None),
            region_index,
        )

        address_lines: list[str] = []
        for entry in region_entries:
            address_lines.append(self._format_address(snapshot, entry.stack_address))

        byte_lines = [entry.slot_bytes.hex_bytes or "-" for entry in region_entries]
        info_lines = self._stack_region_info_lines(snapshot, stack_region, region_entries, metrics)

        return StackVisualRegion(
            address_lines=address_lines or ["-"],
            byte_lines=byte_lines or ["-"],
            info_lines=info_lines or ["stack value"],
            color_code=color,
        )

    def _stack_region_info_lines(self, snapshot, stack_region, region_entries, metrics: RenderMetrics) -> list[str]:
        labels: list[str] = [stack_region.label]

        if region_entries and not metrics.compact:
            symbol_hint = self._stack_symbol_hint(region_entries[0])
            if symbol_hint:
                labels.append(symbol_hint)

        for entry in region_entries:
            if entry.string and not metrics.compact:
                labels.append(f'string "{self._truncate(entry.string, 28)}"')
                break

        if stack_region.kind == "stack-data" and len(region_entries) > 1 and not metrics.compact:
            labels.append(f"size {stack_region.size} bytes")

        for entry in region_entries:
            if entry.pointee_region_id and not self._looks_like_saved_rbp(snapshot, entry):
                offset = entry.pointee_region_offset or 0
                labels.append(f"region {offset:#x}")
                break

        if stack_region.kind == "stack-data" and not metrics.compact:
            ascii_preview = stack_region.bytes_block.ascii_preview.strip(".")
            if ascii_preview:
                labels.append(self._truncate(ascii_preview, 28))

        seen: set[str] = set()
        unique_labels: list[str] = []
        for label in labels:
            if not label or label in seen:
                continue
            unique_labels.append(label)
            seen.add(label)
            if metrics.compact and len(unique_labels) >= 1:
                break
        return unique_labels

    def _build_box_lines(
        self,
        lines: list[str],
        inner_width: int,
        color_code: int,
        align: str = "left",
    ) -> list[str]:
        if not lines:
            return [" " * (inner_width + 4)]

        top = (
            _colorize(BOX_TOP_LEFT, color_code, bold=False)
            + _colorize(BOX_HORIZONTAL * (inner_width + 2), color_code, bold=False)
            + _colorize(BOX_TOP_RIGHT, color_code, bold=False)
        )
        bottom = (
            _colorize(BOX_BOTTOM_LEFT, color_code, bold=False)
            + _colorize(BOX_HORIZONTAL * (inner_width + 2), color_code, bold=False)
            + _colorize(BOX_BOTTOM_RIGHT, color_code, bold=False)
        )
        middle = [
            _colorize(BOX_VERTICAL, color_code, bold=False)
            + " "
            + self._align_visual(line, inner_width, align=align)
            + " "
            + _colorize(BOX_VERTICAL, color_code, bold=False)
            for line in lines
        ]
        return [top, *middle, bottom]

    def _build_address_lines(self, address_lines: list[str], byte_box_line_count: int) -> list[str]:
        if byte_box_line_count <= 0:
            return list(address_lines)

        lines = [""] * byte_box_line_count
        for index, address in enumerate(address_lines):
            target_index = index + 1
            if target_index >= byte_box_line_count - 1:
                target_index = min(byte_box_line_count - 2, target_index)
            if 0 <= target_index < len(lines):
                lines[target_index] = address
        return lines

    def _format_address(self, snapshot, address: int) -> str:
        text = f"{address:#x}"
        if snapshot.stack_pointer is not None and address == snapshot.stack_pointer:
            return _colorize(text, SP_ADDRESS_COLOR)
        return text

    def _stack_symbol_hint(self, entry) -> Optional[str]:
        if not entry.symbol:
            return None

        symbol = entry.symbol
        symbol = symbol.replace(" + ", "+")
        symbol = symbol.split(" in section ", 1)[0]
        symbol = symbol.split(" at ", 1)[0]
        return self._truncate(symbol, 24)

    def _looks_like_saved_rbp(self, snapshot, entry) -> bool:
        if entry.value is None or snapshot.stack_pointer is None:
            return False
        if entry.symbol:
            return False

        if snapshot.entries:
            entry_start = min(item.stack_address for item in snapshot.entries)
            entry_end = max(item.stack_address for item in snapshot.entries) + snapshot.pointer_size
        else:
            entry_start = snapshot.stack_pointer
            entry_end = snapshot.stack_pointer + snapshot.pointer_size
        stack_start = entry_start - (snapshot.pointer_size * 2)
        stack_end = entry_end + (snapshot.pointer_size * 2)
        return stack_start <= entry.value <= stack_end and entry.value % snapshot.pointer_size == 0

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."

    def _center_block(self, lines: list[str], metrics: RenderMetrics) -> list[str]:
        centered_lines = [self._align_visual(line, metrics.width, align="center") for line in lines]
        top_padding = max(0, (metrics.height - len(centered_lines)) // 2)
        if top_padding <= 0:
            return centered_lines
        return ([""] * top_padding) + centered_lines

    def _visual_width(self, value: str) -> int:
        return len(ANSI_ESCAPE_RE.sub("", value))

    def _pad_visual(self, value: str, width: int) -> str:
        visible = self._visual_width(value)
        if visible >= width:
            return value
        return value + (" " * (width - visible))

    def _align_visual(self, value: str, width: int, align: str = "left") -> str:
        visible = self._visual_width(value)
        if visible >= width:
            return value

        if align == "center":
            left = (width - visible) // 2
            right = width - visible - left
            return (" " * left) + value + (" " * right)

        if align == "right":
            return (" " * (width - visible)) + value

        return value + (" " * (width - visible))


class TmuxCommandError(RuntimeError):
    pass


class TmuxStackViewer:
    def __init__(
        self,
        session_name: str = "dbg-stack-viewer",
        window_name: str = "stack",
        store: Optional[SnapshotFileStore] = None,
        refresh_interval: float = 0.4,
    ):
        self.session_name = session_name
        self.window_name = window_name
        self.store = store or SnapshotFileStore(namespace=session_name)
        self.refresh_interval = refresh_interval
        self.layout: Optional[TmuxLayout] = None

    def update(self, snapshot) -> TmuxLayout:
        layout = self.ensure_layout()
        metrics = self.get_render_metrics()
        self.store.write_snapshot(snapshot, metrics)
        self.refresh_titles(snapshot)
        return self.layout or layout

    def update_from_context(
        self,
        context,
        count: int = 20,
        deref_bytes: int = 32,
        direction: str = "up",
    ) -> TmuxLayout:
        self.ensure_layout()
        metrics = self.get_render_metrics()
        effective_count = self._fit_entry_count(context, metrics, count, deref_bytes, direction)
        snapshot = context.get_snapshot(
            count=effective_count,
            deref_bytes=deref_bytes,
            direction=direction,
        )
        return self.update(snapshot)

    def ensure_layout(self) -> TmuxLayout:
        if shutil.which("tmux") is None:
            raise TmuxCommandError("tmux is not installed or not available in PATH")

        if self.layout is not None and self._pane_exists(self.layout.stack_pane):
            return self.layout

        if os.environ.get("TMUX"):
            source_pane = self._resolve_live_source_pane()
            if not source_pane or not self._pane_exists(source_pane):
                source_pane = self._ensure_source_pane()
            if not source_pane or not self._pane_exists(source_pane):
                raise TmuxCommandError("Unable to resolve or create a live tmux source pane")
            panes = self._list_window_panes(source_pane)
            source_info = next((pane for pane in panes if pane["pane_id"] == source_pane), None)
            source_width = int(source_info["width"]) if source_info is not None else self._pane_dimensions(source_pane)[0]
            reusable_pane = self._find_reusable_pane(source_pane, panes)

            if reusable_pane is not None:
                stack_pane = reusable_pane["pane_id"]
                current_target_width = int(reusable_pane["width"])
                viewer_width = self._target_pane_width(source_width + current_target_width)
                self._resize_stack_pane(stack_pane, viewer_width)
            else:
                viewer_width = self._target_pane_width(source_width)
                stack_pane = self._capture_pane_id(
                    self._run_tmux(
                        [
                            "split-window",
                            "-h",
                            "-d",
                            "-l",
                            str(viewer_width),
                            "-P",
                            "-F",
                            "#{pane_id}",
                            "-t",
                            source_pane,
                        ]
                    )
                )
            session_name = self._run_tmux(["display-message", "-p", "-t", source_pane, "#{session_name}"]).strip()
            window_name = self._run_tmux(["display-message", "-p", "-t", source_pane, "#{window_name}"]).strip()
        else:
            if not self._session_exists():
                self._run_tmux(["new-session", "-d", "-s", self.session_name, "-n", self.window_name])
            elif not self._window_exists():
                self._run_tmux(["new-window", "-d", "-t", self.session_name, "-n", self.window_name])

            target = f"{self.session_name}:{self.window_name}"
            stack_pane = self._capture_pane_id(
                self._run_tmux(["display-message", "-p", "-t", target, "#{pane_id}"])
            )
            session_name = self.session_name
            window_name = self.window_name

        self.layout = TmuxLayout(
            session_name=session_name or self.session_name,
            window_name=window_name or self.window_name,
            stack_pane=stack_pane,
        )
        self._start_pane_renderer(stack_pane, self.store.files.stack, "STACK MEMORY VIEW")
        return self.layout

    def get_render_metrics(self) -> RenderMetrics:
        layout = self.ensure_layout()
        width, height = self._pane_dimensions(layout.stack_pane)
        return RenderMetrics(
            width=max(width, 40),
            height=max(height, 8),
            compact=width < 96 or height < 18,
        )

    def refresh_titles(self, snapshot) -> None:
        if self.layout is None or not self._pane_exists(self.layout.stack_pane):
            self.layout = None
            try:
                self.ensure_layout()
            except TmuxCommandError:
                return

        if self.layout is None:
            return

        self._run_tmux(
            [
                "select-pane",
                "-t",
                self.layout.stack_pane,
                "-T",
                f"stack:{snapshot.function_name or '-'}:{len(snapshot.entries)}",
            ],
            check=False,
        )

    def attach(self) -> None:
        self.ensure_layout()
        subprocess.run(["tmux", "attach-session", "-t", self.layout.session_name], check=False)

    def close(self) -> None:
        if self.layout is None:
            return
        self._run_tmux(["kill-pane", "-t", self.layout.stack_pane], check=False)
        self.layout = None

    def focus(self, attach_if_needed: bool = False) -> None:
        self.ensure_layout()

        if os.environ.get("TMUX"):
            return

        if attach_if_needed:
            self.attach()

    def _start_pane_renderer(self, pane_id: str, file_path: Path, title: str) -> None:
        file_literal = str(file_path).replace('"', '\\"')
        quoted_title = title.replace('"', '\\"')
        command = (
            "sh -c '"
            f'last=\"\"; '
            f"while true; do "
            f"current=$(stat -f %m \"{file_literal}\" 2>/dev/null || stat -c %Y \"{file_literal}\" 2>/dev/null || echo 0); "
            f"if [ \"$current\" != \"$last\" ]; then "
            f"printf \"\\033[2J\\033[H[{quoted_title}]\\n\\n\"; "
            f"cat \"{file_literal}\"; "
            f"last=\"$current\"; "
            f"fi; "
            f"sleep {self.refresh_interval}; "
            "done'"
        )
        self._run_tmux(["respawn-pane", "-k", "-t", pane_id, command])

    def _session_exists(self) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.session_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _window_exists(self) -> bool:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", self.session_name, "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False
        return self.window_name in result.stdout.splitlines()

    def _pane_exists(self, pane_id: str) -> bool:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id, "#{pane_id}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _capture_pane_id(self, output: str) -> str:
        pane_id = output.strip()
        if not pane_id:
            raise TmuxCommandError("Failed to capture tmux pane id")
        return pane_id

    def _pane_dimensions(self, pane_id: str) -> tuple[int, int]:
        output = self._run_tmux(
            ["display-message", "-p", "-t", pane_id, "#{pane_width} #{pane_height}"],
            check=False,
        ).strip()
        width_height = self._parse_width_height_line(output)
        if width_height is not None:
            return width_height

        fallback_output = self._run_tmux(
            ["list-panes", "-t", pane_id, "-F", "#{pane_width} #{pane_height}"],
            check=False,
        ).strip()
        for line in fallback_output.splitlines():
            width_height = self._parse_width_height_line(line)
            if width_height is not None:
                return width_height

        return (80, 24)

    def _parse_width_height_line(self, line: str) -> Optional[tuple[int, int]]:
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            width = int(parts[0])
            height = int(parts[1])
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None
        return (width, height)

    def _list_window_panes(self, pane_id: str) -> list[dict[str, str]]:
        output = self._run_tmux(
            [
                "list-panes",
                "-t",
                pane_id,
                "-F",
                "#{pane_id}\t#{pane_width}\t#{pane_height}\t#{pane_active}\t#{pane_title}",
            ]
        ).strip()
        panes: list[dict[str, str]] = []
        for line in output.splitlines():
            pane_id_value, width, height, active, title = (line.split("\t", 4) + [""])[:5]
            panes.append(
                {
                    "pane_id": pane_id_value,
                    "width": width,
                    "height": height,
                    "active": active,
                    "title": title,
                }
            )
        return panes

    def _resolve_live_source_pane(self) -> Optional[str]:
        candidates: list[str] = []
        env_pane = os.environ.get("TMUX_PANE")
        if env_pane:
            candidates.append(env_pane)

        current_pane = self._run_tmux(["display-message", "-p", "#{pane_id}"], check=False).strip()
        if current_pane:
            candidates.append(current_pane)

        list_output = self._run_tmux(
            ["list-panes", "-F", "#{pane_id}\t#{pane_active}"],
            check=False,
        ).strip()
        for line in list_output.splitlines():
            pane_id, active = (line.split("\t", 1) + ["0"])[:2]
            if pane_id and active == "1":
                candidates.append(pane_id)
        for line in list_output.splitlines():
            pane_id = (line.split("\t", 1) + [""])[0]
            if pane_id:
                candidates.append(pane_id)

        seen: set[str] = set()
        for pane_id in candidates:
            if pane_id in seen:
                continue
            seen.add(pane_id)
            if self._pane_exists(pane_id):
                return pane_id
        return None

    def _ensure_source_pane(self) -> Optional[str]:
        source_pane = self._resolve_live_source_pane()
        if source_pane is not None:
            return source_pane

        session_name = self._run_tmux(["display-message", "-p", "#{session_name}"], check=False).strip()
        if not session_name:
            return None

        window_name = self._run_tmux(["display-message", "-p", "#{window_name}"], check=False).strip()
        target = f"{session_name}:{window_name}" if window_name else f"{session_name}:{self.window_name}"

        pane_id = self._run_tmux(["display-message", "-p", "-t", target, "#{pane_id}"], check=False).strip()
        if pane_id and self._pane_exists(pane_id):
            return pane_id

        if self.window_name and window_name != self.window_name:
            self._run_tmux(["new-window", "-d", "-t", session_name, "-n", self.window_name], check=False)
            fallback_target = f"{session_name}:{self.window_name}"
            pane_id = self._run_tmux(["display-message", "-p", "-t", fallback_target, "#{pane_id}"], check=False).strip()
            if pane_id and self._pane_exists(pane_id):
                return pane_id

        return None

    def _find_reusable_pane(self, source_pane: str, panes: list[dict[str, str]]) -> Optional[dict[str, str]]:
        other_panes = [pane for pane in panes if pane["pane_id"] != source_pane]
        if not other_panes:
            return None

        titled_match = next(
            (
                pane
                for pane in other_panes
                if pane["title"].startswith("stack:") or pane["title"] == "STACK MEMORY VIEW"
            ),
            None,
        )
        if titled_match is not None:
            return titled_match

        return max(other_panes, key=lambda pane: int(pane["width"]) * int(pane["height"]))

    def _resize_stack_pane(self, pane_id: str, width: int) -> None:
        self._run_tmux(["resize-pane", "-x", str(width), "-t", pane_id], check=False)

    def _target_pane_width(self, source_width: int) -> int:
        if source_width <= 80:
            return max(32, source_width // 2)
        return max(40, min(96, source_width // 2))

    def _adaptive_entry_count(self, metrics: RenderMetrics, requested_count: int, direction: str = "up") -> int:
        if metrics.compact:
            visible_count = max(4, (metrics.height - 6) // 4)
        else:
            visible_count = max(6, (metrics.height - 6) // 5)
        if direction == "center" and visible_count > 1 and visible_count % 2 == 0:
            visible_count -= 1
        if requested_count <= 0:
            return visible_count
        effective_count = max(1, min(requested_count, visible_count))
        if direction == "center" and effective_count > 1 and effective_count % 2 == 0:
            effective_count -= 1
        return max(1, effective_count)

    def _fit_entry_count(
        self,
        context,
        metrics: RenderMetrics,
        requested_count: int,
        deref_bytes: int,
        direction: str,
    ) -> int:
        upper_bound = self._entry_search_upper_bound(metrics, requested_count, direction)
        upper_bound = max(1, upper_bound)
        best_count = 1
        low = 1
        high = upper_bound

        while low <= high:
            mid = (low + high) // 2
            mid = self._normalize_entry_count(mid, direction)
            if mid < low:
                mid = low
            if mid > high:
                mid = self._normalize_entry_count(high, direction)

            snapshot = context.get_snapshot(
                count=mid,
                deref_bytes=deref_bytes,
                direction=direction,
            )
            rendered_height = self.store.measure_snapshot_height(snapshot, metrics)

            if rendered_height <= metrics.height:
                best_count = mid
                low = mid + 1
            else:
                high = mid - 1

        return max(1, self._normalize_entry_count(best_count, direction))

    def _entry_search_upper_bound(self, metrics: RenderMetrics, requested_count: int, direction: str) -> int:
        base = self._adaptive_entry_count(metrics, requested_count if requested_count > 0 else 0, direction)
        if requested_count > 0:
            return max(1, requested_count)

        if metrics.compact:
            return max(base, metrics.height * 2)
        return max(base, metrics.height * 3)

    def _normalize_entry_count(self, count: int, direction: str) -> int:
        normalized = max(1, count)
        if direction == "center" and normalized > 1 and normalized % 2 == 0:
            normalized -= 1
        return max(1, normalized)

    def _run_tmux(self, args: list[str], check: bool = True) -> str:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown tmux error"
            raise TmuxCommandError(message)
        return result.stdout
