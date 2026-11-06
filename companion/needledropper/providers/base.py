"""Provider contract (docs/SPEC.md 5.4). Shared by engine and all providers."""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import Drop


class ProviderError(Exception):
    """This attempt failed; the engine may try the next provider."""


class ProviderUnavailable(ProviderError):
    """Provider cannot work this session (not configured, not Premium, ...)."""


@dataclass
class NowPlaying:
    """What the deck title card should show for a drop."""

    title: str
    artist: str
    label: str = ""
    art_url: Optional[str] = None
    link: Optional[str] = None
    attribution: Optional[str] = None  # e.g. "Spotify" -> deck shows "Playing on Spotify"
    provider: str = ""
    hint: Optional[str] = None  # e.g. "Press play in Apple Music"


@dataclass
class PlayHandle:
    provider: str
    drop: Drop
    started_at: float = field(default_factory=time.monotonic)
    data: dict[str, Any] = field(default_factory=dict)
    now_playing: Optional[NowPlaying] = None


class Provider(abc.ABC):
    """A music source. Methods are called from the engine thread only."""

    name: str = "base"

    @abc.abstractmethod
    def available(self) -> bool:
        """Configured and usable right now (cheap; no network)."""

    @abc.abstractmethod
    def can_play(self, drop: Drop) -> bool:
        """This provider has (or can resolve) something to play for `drop`."""

    def prepare(self, drop: Drop) -> None:
        """Best-effort prewarm on ARM (wake device, resolve track). Must never raise."""

    @abc.abstractmethod
    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        """Start playback. Raise ProviderError/ProviderUnavailable on failure."""

    @abc.abstractmethod
    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        """Stop playback started by `play` and restore whatever we disturbed. Must not raise."""

    def poll(self, handle: PlayHandle) -> str:
        """Return "playing", "ended" or "error". Default: rely on engine's duration timer."""
        return "playing"

    def close(self) -> None:
        """Release resources at shutdown."""
