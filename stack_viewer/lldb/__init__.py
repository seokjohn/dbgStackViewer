from __future__ import annotations

from typing import Optional

import lldb

from stack_viewer.context import StackViewerController, ViewerOptions

from .stack import ByteBlock, StackContext, StackEntry, StackMemoryRegion, StackRegion, StackSnapshot


BACKEND_NAME = "lldb"


def resolve_debugger(debugger=None):
    return debugger or getattr(lldb, "debugger", None)


def get_current_frame(debugger=None):
    active_debugger = resolve_debugger(debugger)
    if active_debugger is None:
        return None

    target = active_debugger.GetSelectedTarget()
    if not target or not target.IsValid():
        return None

    process = target.GetProcess()
    if not process or not process.IsValid():
        return None

    thread = process.GetSelectedThread()
    if not thread or not thread.IsValid():
        return None

    frame = thread.GetSelectedFrame()
    return frame if frame and frame.IsValid() else None


def create_context(debugger=None) -> StackContext:
    return StackContext(resolve_debugger(debugger))


def create_controller(
    debugger=None,
    options: Optional[ViewerOptions] = None,
) -> StackViewerController:
    viewer_options = options or ViewerOptions(session_name="dbg-stack-viewer-lldb")
    return StackViewerController(
        context_factory=lambda: create_context(debugger=debugger),
        options=viewer_options,
    )


def show_stack(debugger=None, options: Optional[ViewerOptions] = None):
    return create_controller(debugger=debugger, options=options).show_once()


def show_stack_detail(debugger=None, options: Optional[ViewerOptions] = None):
    return create_controller(debugger=debugger, options=options).show_below_sp_detail()


__all__ = [
    "BACKEND_NAME",
    "ByteBlock",
    "StackContext",
    "StackEntry",
    "StackMemoryRegion",
    "StackRegion",
    "StackSnapshot",
    "ViewerOptions",
    "StackViewerController",
    "create_context",
    "create_controller",
    "get_current_frame",
    "resolve_debugger",
    "show_stack",
    "show_stack_detail",
]
