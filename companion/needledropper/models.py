"""Shared data model for NeedleDropper (see docs/SPEC.md section 3).

This module is a cross-component contract. Do not change field meanings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import Any, Optional


class EventType(IntEnum):
    NONE = 0
    HELLO = 1
    ARM = 2
    FIRE = 3
    CANCEL = 4
    PING = 5


class Flags(IntFlag):
    NONE = 0
    IN_COMBAT = 0x01
    IN_INSTANCE = 0x02
    ON_TAXI = 0x04
    DEAD = 0x08
    LOCAL_PLAYBACK = 0x10
    DROP_ACTIVE = 0x20
    TEST = 0x40


@dataclass(frozen=True)
class Event:
    """One addon -> companion event, as decoded from either transport."""

    type: EventType
    seq: int  # 0..255
    n: int  # 0..65535, drop number within the map identified by `hash`
    hash: int  # 0..65535, Map.lua hash the addon has loaded
    flags: Flags = Flags.NONE
    id: str = ""  # drop id string (chat-log channel only; "" from the beacon)

    @property
    def key(self) -> tuple[int, int, int, int]:
        """Dedupe key shared by both transports."""
        return (self.seq, int(self.type), self.n, self.hash)

    def has(self, flag: Flags) -> bool:
        return bool(self.flags & flag)


@dataclass
class Drop:
    """A resolved needle drop (one entry of Map.json plus pack-only fields)."""

    n: int
    id: str
    kind: str  # "zone" | "subzone" | "instance" | "test"
    title: str
    artist: str
    label: str = ""
    mapID: Optional[int] = None
    areaID: Optional[int] = None
    subzone: Optional[str] = None
    instanceID: Optional[int] = None
    durationMs: int = 0
    startMs: int = 0
    volume: Optional[int] = None  # 0..100
    spotify: Optional[str] = None  # spotify:track:<id>
    youtube: Optional[str] = None  # video id
    appleMusic: Optional[str] = None  # https://music.apple.com/... url
    file: Optional[str] = None  # companion-side local file (relative to local.mediaDir or absolute)
    inGameFile: Optional[str] = None  # WoW path for in-game playback (Map.lua `file`)
    note: str = ""
    pack: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_label(self) -> str:
        return self.label or self.subzone or self.id

    def to_map_entry(self) -> dict[str, Any]:
        """The subset written into Map.lua / Map.json (SPEC 3.2)."""
        out: dict[str, Any] = {"n": self.n, "id": self.id, "kind": self.kind}
        for k in ("mapID", "areaID", "subzone", "instanceID"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        out["label"] = self.display_label
        out["title"] = self.title
        out["artist"] = self.artist
        out["durationMs"] = int(self.durationMs or 0)
        out["startMs"] = int(self.startMs or 0)
        if self.inGameFile:
            out["file"] = self.inGameFile
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Drop":
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        kwargs = {k: v for k, v in d.items() if k in known}
        if "file" in d and "inGameFile" not in d and str(d.get("file", "")).lower().startswith("interface\\"):
            # Map.json `file` is the in-game path.
            kwargs.pop("file", None)
            kwargs["inGameFile"] = d["file"]
        extra = {k: v for k, v in d.items() if k not in known}
        kwargs.setdefault("title", "")
        kwargs.setdefault("artist", "")
        kwargs.setdefault("kind", "zone")
        kwargs.setdefault("n", 0)
        kwargs.setdefault("id", "")
        return cls(extra=extra, **kwargs)


TEST_DROP = Drop(n=0, id="test", kind="test", title="NeedleDropper test", artist="NeedleDropper", label="NeedleDropper Test")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """SPEC 3.1 slug: lowercase, runs of non [a-z0-9] -> '-', trimmed."""
    return _SLUG_RE.sub("-", name.lower()).strip("-")


def drop_id_for(*, mapID: Optional[int] = None, subzone: Optional[str] = None,
                areaID: Optional[int] = None, instanceID: Optional[int] = None) -> str:
    """Derive the stable drop id (SPEC 3.1 / 3.7 precedence: instance > area > subzone > zone)."""
    if instanceID is not None:
        return f"inst:{int(instanceID)}"
    if areaID is not None:
        return f"area:{int(areaID)}"
    if subzone:
        if mapID is None:
            raise ValueError("subzone drop needs a zone (mapID)")
        return f"sub:{int(mapID)}:{slugify(subzone)}"
    if mapID is None:
        raise ValueError("drop needs a zone, subzone, areaID or instanceID")
    return f"zone:{int(mapID)}"
