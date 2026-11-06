"""Local audio files played in the companion's player window (docs/SPEC.md 5.4 ``local``).

``drop.file`` is resolved against ``local.mediaDir`` (or used as-is when
absolute); as a convenience a file the user copied into
``Interface\\AddOns\\NeedleDropper_Local\\media`` for in-game playback is found
there too. The player page plays it with HTML5 ``<audio>`` (seek to
``startMs``, JS volume fades).

This provider never opens a window (the in-game title card is the only popup,
SPEC-v1.1 A): it plays only while a player window is already open -- e.g.
left open after a YouTube drop, or opened from the control panel. In-game
playback (``inGameFile`` + the addon) needs no window at all and is preferred.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from ..config import DEFAULT_CONFIG, expand_path
from ..models import Drop
from .base import NowPlaying, PlayHandle, Provider, ProviderError

log = logging.getLogger("needledropper.providers.local")

START_STATES = {"playing", "error", "autoplay-blocked", "ended"}
AUTOPLAY_HINT = "Click the NeedleDropper player window once to enable sound"


def deck_not_open_message(deck: Any) -> str:
    url = getattr(deck, "url", "http://127.0.0.1:8322/") if deck is not None else "http://127.0.0.1:8322/"
    return (f"the player window is not open -- open it from the control panel ({url}); "
            "it plays local files and YouTube")


class LocalProvider(Provider):
    name = "local"

    def __init__(self, config_section: Optional[dict[str, Any]], paths: Any, deck: Any, *,
                 start_timeout: float = 4.0) -> None:
        self.config = dict(config_section or {})
        self.paths = paths
        self.deck = deck
        self.start_timeout = start_timeout
        self.media_dir = expand_path(self.config.get("mediaDir") or DEFAULT_CONFIG["local"]["mediaDir"])

    # ---- helpers
    def resolve_file(self, drop: Drop) -> Optional[Path]:
        """The companion-side file for ``drop`` if it exists, else None."""
        if not drop.file:
            return None
        raw = expand_path(drop.file)
        candidates = [raw] if raw.is_absolute() else [self.media_dir / raw]
        local_media = getattr(self.paths, "local_media_dir", None)
        if local_media is not None and not raw.is_absolute():
            candidates.append(Path(local_media) / raw)
        for c in candidates:
            try:
                if c.is_file():
                    return c
            except OSError:
                continue
        return None

    # ---- Provider
    def available(self) -> bool:
        return bool(self.config.get("enabled", True)) and self.deck is not None and bool(self.deck.connected)

    def can_play(self, drop: Drop) -> bool:
        return self.resolve_file(drop) is not None

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        if self.deck is None or not self.deck.connected:
            raise ProviderError(deck_not_open_message(self.deck))
        path = self.resolve_file(drop)
        if path is None:
            raise ProviderError(f"audio file {drop.file!r} not found in {self.media_dir}")
        url = self.deck.register_media(path)
        play_id = uuid.uuid4().hex
        self.deck.send({
            "cmd": "play", "playId": play_id, "source": "local", "url": url, "dropId": drop.id,
            "startMs": int(drop.startMs or 0), "fadeInMs": int(fade_in_ms or 0),
            "volume": int(drop.volume) if drop.volume is not None else 100,
        })
        st = self.deck.wait_status(play_id=play_id, states=START_STATES, timeout=self.start_timeout)
        hint = None
        if st is not None and st.get("state") == "error":
            raise ProviderError(f"the player could not play {path.name}: {st.get('detail') or 'error'}")
        if st is not None and st.get("state") == "autoplay-blocked":
            hint = AUTOPLAY_HINT
        elif st is None:
            log.info("the player did not confirm playback of %s yet; assuming it is starting", path.name)
        np = NowPlaying(title=drop.title, artist=drop.artist, label=drop.display_label,
                        art_url=drop.extra.get("artUrl") if isinstance(drop.extra.get("artUrl"), str) else None,
                        provider=self.name, hint=hint)
        return PlayHandle(provider=self.name, drop=drop, data={"playId": play_id, "path": str(path)},
                          now_playing=np)

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        if self.deck is None:
            return
        try:
            self.deck.send({"cmd": "stop", "playId": handle.data.get("playId"), "fadeOutMs": int(fade_out_ms or 0)})
        except Exception as exc:  # must not raise
            log.debug("stop failed: %s", exc)

    def poll(self, handle: PlayHandle) -> str:
        if self.deck is None:
            return "ended"
        st = self.deck.status_for(handle.data.get("playId", ""))
        if st is not None:
            state = st.get("state")
            if state == "ended":
                return "ended"
            if state == "error":
                return "error"
        if not self.deck.connected:
            return "ended"  # window closed: nothing is playing any more
        return "playing"
