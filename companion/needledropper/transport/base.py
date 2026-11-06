"""Transport contract (docs/SPEC.md 5.2): addon -> companion event readers."""
from __future__ import annotations

import abc
from typing import Any, Callable

from ..models import Event

EventCallback = Callable[[Event, str], None]  # (event, transport name)


class Transport(abc.ABC):
    """Reads events produced by the addon. Runs its own daemon thread.

    ctor(config_section: dict, paths, on_event): `paths` is config.Paths
    (attributes: wow_root, flavor_root, addons_dir, addon_dir, chat_log, game_exe, app_dir).
    """

    name: str = "base"

    def __init__(self, config_section: dict[str, Any], paths: Any, on_event: EventCallback) -> None:
        self.config = config_section
        self.paths = paths
        self.on_event = on_event

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> dict[str, Any]:
        """Human-readable health info, e.g. {"ok": True, "lastFrame": ..., "detail": "..."}."""
