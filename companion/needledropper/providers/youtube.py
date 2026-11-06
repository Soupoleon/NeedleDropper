"""YouTube videos played in the companion's player window (docs/SPEC.md 5.4, SPEC-v1.1 C.3).

YouTube's rules require the player to stay visible while it plays. The window is made once,
hidden, when the app starts (deck.prepare_player; real-client findings #8 / #13: Chromium
activates a window when it creates it, so none is created while the player is in the game),
shown always on top at its corner -- never taking the focus -- while a YouTube song plays, and
hidden again afterwards (deck/window.py).

On ARM :meth:`prepare` cues the song in that window (``cueVideoById``: loaded, paused, silent),
so FIRE only has to start it. When no window is connected at FIRE, the deck may open one (not
while the game is in front) and this provider waits up to 8 s for it. With ``deck.autoOpen`` =
``"never"``, or if the window does not connect in time, :meth:`play` raises ProviderError so the
engine tries the next provider. Embedding errors (100/101/150/153) are reported by the page;
they make this provider fail too -- immediately if the page reports within ``start_timeout``,
otherwise via :meth:`poll`.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from ..models import Drop
from .base import NowPlaying, PlayHandle, Provider, ProviderError
from .local import AUTOPLAY_HINT, START_STATES, deck_not_open_message

log = logging.getLogger("needledropper.providers.youtube")

PLAYER_CONNECT_TIMEOUT = 8.0

YT_ERRORS = {
    2: "invalid video id",
    5: "HTML5 player error",
    100: "video not found or private",
    101: "the owner does not allow embedding",
    150: "the owner does not allow embedding",
    153: "embed rejected (missing referrer/origin)",
}


def watch_url(video_id: str, start_ms: int = 0) -> str:
    t = int(start_ms // 1000) if start_ms else 0
    return f"https://www.youtube.com/watch?v={video_id}" + (f"&t={t}s" if t else "")


class YouTubeProvider(Provider):
    name = "youtube"

    def __init__(self, config_section: Optional[dict[str, Any]], paths: Any, deck: Any, *,
                 start_timeout: float = 8.0, connect_timeout: float = PLAYER_CONNECT_TIMEOUT) -> None:
        self.config = dict(config_section or {})
        self.paths = paths
        self.deck = deck
        self.start_timeout = start_timeout
        self.connect_timeout = connect_timeout

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _player_open(self) -> bool:
        deck = self.deck
        if deck is None:
            return False
        return bool(getattr(deck, "page_open", getattr(deck, "connected", False)))

    def _can_open_player(self) -> bool:
        deck = self.deck
        if deck is None or not hasattr(deck, "ensure_player"):
            return False
        return getattr(deck, "auto_open", "youtube") != "never" or bool(getattr(deck, "player_opening", False))

    def available(self) -> bool:
        """Enabled, and a player window is open or may be opened for the drop."""
        if not self.enabled or self.deck is None:
            return False
        return self._player_open() or bool(getattr(self.deck, "connected", False)) or self._can_open_player()

    def can_play(self, drop: Drop) -> bool:
        return self.enabled and bool(drop.youtube)

    def _ensure_player(self) -> None:
        """Open (if allowed) and wait for the player window; ProviderError if there is none."""
        deck = self.deck
        if deck is None:
            raise ProviderError(deck_not_open_message(deck))
        if self._player_open():
            return
        ensure = getattr(deck, "ensure_player", None)
        if ensure is None:  # a deck without window management (tests, tools)
            if not getattr(deck, "connected", False):
                raise ProviderError(deck_not_open_message(deck))
            return
        if ensure(self.connect_timeout):
            return
        if getattr(deck, "auto_open", "youtube") == "never" and not getattr(deck, "player_opening", False):
            raise ProviderError("the player window is closed and opening it automatically is off "
                                "(deck.autoOpen is \"never\")")
        front = getattr(deck, "game_in_front", None)
        try:
            in_front = bool(front()) if callable(front) else False
        except Exception:  # noqa: BLE001
            in_front = False
        if in_front and not getattr(deck, "player_opening", False):
            raise ProviderError("there is no player window, and none is made while the game is in front (it would "
                                "take the focus); it is made again once you are out of the game")
        raise ProviderError(f"the YouTube player window did not connect within {self.connect_timeout:g} s")

    def prepare(self, drop: Drop) -> None:
        """ARM pre-warm: make sure the player window is there (never created while the game is in
        front: the deck refuses) and cue the video in it, so FIRE only starts it. Never raises."""
        if not (self.enabled and drop.youtube and self.deck is not None):
            return
        try:
            if not self._player_open():
                ensure = getattr(self.deck, "ensure_player", None)
                if ensure is None or not self._can_open_player() or not ensure(self.connect_timeout):
                    return
            self.deck.send({"cmd": "cue", "videoId": drop.youtube, "dropId": drop.id,
                            "startMs": int(drop.startMs or 0)})
        except Exception as exc:  # noqa: BLE001 - best effort by contract
            log.debug("YouTube prewarm for %s failed: %s", drop.id, exc)

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        if not drop.youtube:
            raise ProviderError("drop has no YouTube video id")
        self._ensure_player()
        play_id = uuid.uuid4().hex
        self.deck.send({
            "cmd": "play", "playId": play_id, "source": "youtube", "videoId": drop.youtube, "dropId": drop.id,
            "startMs": int(drop.startMs or 0), "fadeInMs": int(fade_in_ms or 0),
            "volume": int(drop.volume) if drop.volume is not None else 100,
        })
        st = self.deck.wait_status(play_id=play_id, states=START_STATES, timeout=self.start_timeout)
        hint = None
        if st is not None and st.get("state") == "error":
            raise ProviderError(self.describe_error(st))
        if st is not None and st.get("state") == "autoplay-blocked":
            hint = AUTOPLAY_HINT
        elif st is None:
            log.info("YouTube player has not started %s yet; still waiting", drop.youtube)
        np = NowPlaying(title=drop.title, artist=drop.artist, label=drop.display_label,
                        art_url=drop.extra.get("artUrl") if isinstance(drop.extra.get("artUrl"), str) else None,
                        link=watch_url(drop.youtube, drop.startMs), attribution="YouTube", provider=self.name,
                        hint=hint)
        return PlayHandle(provider=self.name, drop=drop, data={"playId": play_id, "videoId": drop.youtube},
                          now_playing=np)

    @staticmethod
    def describe_error(st: dict[str, Any]) -> str:
        code = st.get("code")
        if isinstance(code, int) and code in YT_ERRORS:
            return f"YouTube error {code}: {YT_ERRORS[code]}"
        return f"YouTube: {st.get('detail') or 'player error'}"

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
                log.warning("%s", self.describe_error(st))
                return "error"
        if not self.deck.connected:
            return "ended"
        return "playing"
