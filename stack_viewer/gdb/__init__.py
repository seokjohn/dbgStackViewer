from __future__ import annotations

from typing import Optional

import gdb

from stack_viewer.context import StackViewerController, ViewerOptions

from .stack import ByteBlock, StackContext, StackEntry, StackMemoryRegion, StackRegion, StackSnapshot


BACKEND_NAME = "gdb"


def get_current_frame():
    try:
        return gdb.selected_frame()
    except gdb.error:
        return None


def create_context(debugger=None) -> StackContext:
    return StackContext(debugger=debugger)


def create_controller(
    debugger=None,
    options: Optional[ViewerOptions] = None,
) -> StackViewerController:
    viewer_options = options or ViewerOptions(session_name="dbg-stack-viewer-gdb")
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
    "show_stack",
    "show_stack_detail",
]
