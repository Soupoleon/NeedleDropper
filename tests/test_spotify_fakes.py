"""Shared fakes for the Spotify tests (scripted HTTP, fake clock). Contains no tests itself."""
from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper.models import Drop  # noqa: E402
from needledropper.providers.spotify import SpotifyProvider  # noqa: E402
from needledropper.providers.spotify_auth import SCOPES, TokenStore  # noqa: E402


@dataclass
class Call:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Optional[bytes]
    url: str

    @property
    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None

    @property
    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs((self.body or b"").decode("ascii")).items()}

    @property
    def bearer(self) -> str:
        return self.headers.get("Authorization", "")


class FakeHTTP:
    """Scripted stand-in for the ``http`` callable.

    ``add(method, path, *responses)`` queues responses for a route (path as in the
    URL, e.g. "/v1/me/player"); each response is ``(status, body)``,
    ``(status, body, headers)``, an Exception instance (raised), or a callable
    ``f(call) -> response``. Responses are consumed in order; the last one repeats.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[Call] = []
        self._lock = threading.Lock()

    def add(self, method: str, path: str, *responses: Any) -> "FakeHTTP":
        self.routes.setdefault((method.upper(), path), []).extend(responses)
        return self

    def set(self, method: str, path: str, *responses: Any) -> "FakeHTTP":
        self.routes[(method.upper(), path)] = list(responses)
        return self

    def __call__(self, method: str, url: str, headers: dict, body: Optional[bytes]):
        parts = urlsplit(url)
        call = Call(method.upper(), parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()},
                    dict(headers or {}), body, url)
        with self._lock:
            self.calls.append(call)
            queue = self.routes.get((call.method, call.path))
            if not queue:
                resp: Any = (404, {"error": {"status": 404, "message": f"fake: no route {call.method} {call.path}"}})
            elif len(queue) == 1:
                resp = queue[0]
            else:
                resp = queue.pop(0)
        if callable(resp) and not isinstance(resp, tuple):
            resp = resp(call)
        if isinstance(resp, BaseException):
            raise resp
        status, payload = resp[0], resp[1]
        hdrs = {str(k).lower(): str(v) for k, v in (resp[2] if len(resp) > 2 else {}).items()}
        if payload is None:
            raw = b""
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raw = json.dumps(payload).encode("utf-8")
        return status, hdrs, raw

    def find(self, method: str, path: str) -> list[Call]:
        with self._lock:
            return [c for c in self.calls if c.method == method.upper() and c.path == path]

    def puts(self) -> list[Call]:
        with self._lock:
            return [c for c in self.calls if c.method == "PUT"]


class FakeClock:
    """Monotonic clock whose sleep() advances time instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def tid(tag: str) -> str:
    """A syntactically valid 22-char Spotify id."""
    out = (tag + "0" * 22)[:22]
    assert out.isalnum()
    return out


def track(tag: str, name: str, artists: list[str], *, duration: int = 200_000, album: str = "Album",
          images: Optional[list[dict]] = None, **extra: Any) -> dict:
    t = {
        "id": tid(tag),
        "uri": f"spotify:track:{tid(tag)}",
        "name": name,
        "duration_ms": duration,
        "artists": [{"name": a} for a in artists],
        "album": {"name": album, "images": images if images is not None else [
            {"url": f"https://i.scdn.co/{tag}/64", "width": 64, "height": 64},
            {"url": f"https://i.scdn.co/{tag}/640", "width": 640, "height": 640},
            {"url": f"https://i.scdn.co/{tag}/300", "width": 300, "height": 300},
        ]},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{tid(tag)}"},
    }
    t.update(extra)
    return t


PC = {"id": "dev-pc", "name": "GAMING-PC", "type": "Computer", "is_active": False, "is_restricted": False,
      "volume_percent": 70, "supports_volume": True}
PHONE = {"id": "dev-phone", "name": "Pixel", "type": "Smartphone", "is_active": True, "is_restricted": False,
         "volume_percent": 40, "supports_volume": True}


def player_state(item: Optional[dict], progress: int, *, playing: bool = True, device: Optional[dict] = None,
                 context: Optional[dict] = None) -> dict:
    return {"device": dict(device or PC), "item": item, "progress_ms": progress, "is_playing": playing,
            "context": context, "currently_playing_type": "track"}


DROP_TRACK = track("drop", "I'll Keep Coming", ["Low Roar"], duration=245_000)
DROP_URI = DROP_TRACK["uri"]


def make_drop(**kw: Any) -> Drop:
    base = dict(n=1, id="zone:1429", kind="zone", title="I'll Keep Coming", artist="Low Roar",
                label="Elwynn Forest", mapID=1429, spotify=DROP_URI, startMs=0, durationMs=0)
    base.update(kw)
    return Drop(**base)


#: The scopes a v1.2 sign-in carries (the v1.1 set plus user-read-private, real-client finding #3).
#: Tests of an older sign-in pass ``scope=V11_SCOPE`` explicitly.
CURRENT_SCOPE = " ".join(SCOPES)
V11_SCOPE = "user-modify-playback-state user-read-playback-state"


def save_tokens(app_dir: Path, **over: Any) -> TokenStore:
    store = TokenStore(app_dir)
    tokens = {"access_token": "AT1", "refresh_token": "RT1", "expires_at": time.time() + 3600,
              "scope": CURRENT_SCOPE, "client_id": "cid"}
    tokens.update(over)
    store.save(tokens)
    return store


@dataclass
class Rig:
    provider: SpotifyProvider
    http: FakeHTTP
    clock: FakeClock
    started: list[str] = field(default_factory=list)
    app_dir: Path = Path(".")


def make_rig(app_dir: Path, *, cfg: Optional[dict] = None, http: Optional[FakeHTTP] = None,
             startfile: Optional[Callable[[str], Any]] = None, tokens: bool = True) -> Rig:
    if tokens:
        save_tokens(app_dir)
    fake = http or FakeHTTP()
    clock = FakeClock()
    started: list[str] = []

    def _startfile(target: str) -> None:
        started.append(target)
        if startfile:
            startfile(target)

    conf = {"clientId": "cid", "redirectPort": 8321, "deviceName": "", "fade": True, "restorePrevious": True,
            "volume": None}
    conf.update(cfg or {})
    provider = SpotifyProvider(conf, SimpleNamespace(app_dir=app_dir), http=fake, startfile=_startfile,
                               sleep=clock.sleep, clock=clock)
    return Rig(provider=provider, http=fake, clock=clock, started=started, app_dir=app_dir)


def ok_routes(fake: FakeHTTP, *, snapshot: Any = (204, None), devices: Optional[list[dict]] = None,
              playing: Optional[dict] = None) -> FakeHTTP:
    """Happy-path routes: snapshot, then our track playing; one PC device; commands succeed."""
    fake.add("GET", "/v1/me/player", snapshot, (200, playing or player_state(DROP_TRACK, 1200)))
    fake.add("GET", "/v1/me/player/devices", (200, {"devices": devices if devices is not None else [PC]}))
    fake.add("PUT", "/v1/me/player/play", (204, None))
    fake.add("PUT", "/v1/me/player/pause", (204, None))
    fake.add("PUT", "/v1/me/player/volume", (204, None))
    return fake
