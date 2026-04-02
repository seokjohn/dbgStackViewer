#!/usr/bin/env python
from __future__ import annotations

import atexit
from pathlib import Path
import sys

directory = Path(__file__).resolve().parent

if str(directory) not in sys.path:
    sys.path.append(str(directory))

BACKEND = None
BACKEND_NAME = None

if "gdb" in sys.modules:
    import gdb
    import stack_viewer.gdb as BACKEND

    BACKEND_NAME = "gdb"
elif "lldb" in sys.modules:
    import lldb
    import stack_viewer.lldb as BACKEND

    BACKEND_NAME = "lldb"
else:
    raise ImportError("Unknown gdb or lldb")


_CONTROLLER = None


def _extract_debugger(args):
    if BACKEND_NAME == "lldb" and args:
        return args[0]
    return None


def _get_controller(debugger=None):
    global _CONTROLLER

    if _CONTROLLER is None:
        _CONTROLLER = BACKEND.create_controller(debugger=debugger)
    return _CONTROLLER


def stack_viewer_fun(*args):
    debugger = _extract_debugger(args)
    controller = _get_controller(debugger=debugger)
    controller.show_once()
    return None


def stack_detail_viewer_fun(*args):
    debugger = _extract_debugger(args)
    controller = _get_controller(debugger=debugger)
    controller.show_below_sp_detail()
    return None


def _cleanup_stack_viewer(*args):
    global _CONTROLLER

    if _CONTROLLER is None:
        return None

    _CONTROLLER.cleanup()
    _CONTROLLER = None
    return None


atexit.register(_cleanup_stack_viewer)


if "pwndbg" in sys.modules:
    import pwndbg.commands
    from pwndbg.commands import CommandCategory

    @pwndbg.commands.Command(
        "Show the stack viewer once.",
        command_name="sv",
        category=CommandCategory.STACK,
    )
    def pwndbg_stack_viewer_fun():
        return stack_viewer_fun()

    @pwndbg.commands.Command(
        "Show lower stack detail under the current stack pointer.",
        command_name="sd",
        category=CommandCategory.STACK,
    )
    def pwndbg_stack_detail_viewer_fun():
        return stack_detail_viewer_fun()
else:
    if BACKEND_NAME == "gdb":
        class StackViewerCommand(gdb.Command):
            def __init__(self):
                super().__init__("sv", gdb.COMMAND_USER)

            def invoke(self, arg, from_tty):
                stack_viewer_fun()

        class StackDetailViewerCommand(gdb.Command):
            def __init__(self):
                super().__init__("sd", gdb.COMMAND_USER)

            def invoke(self, arg, from_tty):
                stack_detail_viewer_fun()

        StackViewerCommand()
        StackDetailViewerCommand()

    if BACKEND_NAME == "lldb":
        def __lldb_init_module(debugger, internal_dict):
            debugger.HandleCommand("command script add -f dbginit.stack_viewer_fun sv")
            debugger.HandleCommand("command script add -f dbginit.stack_detail_viewer_fun sd")
