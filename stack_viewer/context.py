from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .splitter.tmux import TmuxStackViewer


@dataclass
class ViewerOptions:
    count: int = 20
    deref_bytes: int = 32
    refresh_interval: float = 0.4
    session_name: str = "dbg-stack-viewer"
    window_name: str = "stack"


class StackViewerController:
    def __init__(
        self,
        context_factory: Callable[[], object],
        options: Optional[ViewerOptions] = None,
        viewer_factory: Optional[Callable[[ViewerOptions], TmuxStackViewer]] = None,
    ):
        self.context_factory = context_factory
        self.options = options or ViewerOptions()
        self.viewer_factory = viewer_factory or self._default_viewer_factory
        self.context = None
        self.viewer: Optional[TmuxStackViewer] = None

    def _default_viewer_factory(self, options: ViewerOptions) -> TmuxStackViewer:
        return TmuxStackViewer(
            session_name=options.session_name,
            window_name=options.window_name,
            refresh_interval=options.refresh_interval,
        )

    def ensure_context(self):
        if self.context is None:
            self.context = self.context_factory()
        return self.context

    def ensure_viewer(self) -> TmuxStackViewer:
        if self.viewer is None:
            self.viewer = self.viewer_factory(self.options)
        return self.viewer

    def show_once(self):
        return self.refresh(attach_if_needed=True, direction="up")

    def show_below_sp_detail(self):
        return self.refresh(attach_if_needed=True, count=0, direction="center")

    def refresh(
        self,
        attach_if_needed: bool = False,
        *,
        count: Optional[int] = None,
        deref_bytes: Optional[int] = None,
        direction: str = "up",
    ):
        context = self.ensure_context()
        viewer = self.ensure_viewer()
        layout = viewer.update_from_context(
            context,
            count=self.options.count if count is None else count,
            deref_bytes=self.options.deref_bytes if deref_bytes is None else deref_bytes,
            direction=direction,
        )
        viewer.focus(attach_if_needed=attach_if_needed)
        return layout

    def cleanup(self):
        if self.viewer is not None:
            self.viewer.close()
