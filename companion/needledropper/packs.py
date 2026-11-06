"""Soundtrack packs (``packs/*.json``, docs/SPEC.md 3.7): load, validate, merge.

A pack is user-edited JSON, so every problem is reported with the pack name,
the drop index and a plain-language message, e.g.::

    pack "Azeroth Unbound" drops[3] (Goldshire): missing "artist"

:func:`load_packs` merges packs in order (a later pack overrides an earlier one
for the same drop id, and a ``"disabled": true`` entry switches an earlier
pack's drop off), fills in Spotify resolutions from
``<app_dir>\\cache\\resolved.json`` and numbers the drops (``n``) by sorted id.

The same JSON object is the body of an in-game share code (docs/DESIGN-v1.2.md
5.2; :mod:`needledropper.sharecode`), so the share-code fields are understood
too: the top-level ``id`` (``[a-z0-9-]{1,40}``, derived from the name when
missing), ``tags``, ``homepage`` (http/https) and ``revision`` (whole number
>= 0), and per drop ``tags`` and ``disabled``. A drop with ``"disabled": true``
is skipped without being checked (it may be a bare place that switches another
soundtrack's drop off, or an unfinished draft), exactly like ``tools/gen_sets.py``.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Optional, Sequence

from . import zones
from .models import Drop, drop_id_for, slugify
from .protocol2 import spotify_id_from_link, youtube_id_from_link

PACK_FORMAT = "needledropper-pack"
PACK_VERSION = 1
MAX_DROPS = 65535
MAX_PACK_ID = 40
IN_GAME_MEDIA_PREFIX = "Interface\\AddOns\\NeedleDropper_Local\\media\\"

#: Keys a pack drop may carry (anything else is kept in ``Drop.extra`` with a warning).
KNOWN_DROP_KEYS = {
    "zone", "subzone", "areaID", "instanceID", "label", "title", "artist", "spotify", "youtube",
    "appleMusic", "file", "inGameFile", "startMs", "durationMs", "volume", "note", "id", "tags", "disabled",
}
#: Top-level keys of a pack (DESIGN-v1.2 5.2).
KNOWN_PACK_KEYS = ("format", "version", "id", "name", "author", "description", "tags", "homepage", "revision",
                   "drops")

_SPOTIFY_URI_RE = re.compile(r"^spotify:track:([A-Za-z0-9]{22})$")
_SPOTIFY_URL_RE = re.compile(r"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?track/([A-Za-z0-9]{22})(?:[/?#].*)?$")
_SPOTIFY_BARE_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PACK_ID_RE = re.compile(r"(?=[a-z0-9-]*[a-z0-9])[a-z0-9-]{1,%d}" % MAX_PACK_ID)
_URL_RE = re.compile(r"https?://[^\s\x00-\x1f\x7f]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_YT_URL_RES = [
    re.compile(r"^https?://(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"^https?://youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"^https?://(?:www\.)?youtube(?:-nocookie)?\.com/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})"),
]


@dataclass
class PackIssue:
    """One validation finding."""

    level: str  # "error" | "warning"
    pack: str
    message: str
    index: Optional[int] = None
    hint: str = ""  # short context, e.g. the drop's title or zone
    source: str = ""  # pack file path

    def __str__(self) -> str:
        where = f'pack "{self.pack}"'
        if self.index is not None:
            where += f" drops[{self.index}]"
            if self.hint:
                where += f" ({self.hint})"
        return f"{where}: {self.message}"


class PackError(ValueError):
    """Packs contain errors; ``issues`` holds every finding."""

    def __init__(self, issues: Sequence[PackIssue]):
        self.issues = list(issues)
        errors = [i for i in self.issues if i.level == "error"]
        head = f"{len(errors)} error(s) in packs"
        super().__init__(head + ":\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass
class Pack:
    name: str
    path: Optional[Path]
    author: str = ""
    description: str = ""
    drops: list[Drop] = field(default_factory=list)  # n not yet assigned (0)
    issues: list[PackIssue] = field(default_factory=list)
    #: Soundtrack id: the pack's ``"id"``, else derived from the name ("" if neither works).
    id: str = ""
    tags: list[str] = field(default_factory=list)
    homepage: str = ""
    revision: Optional[int] = None
    #: Ids of the ``"disabled": true`` entries whose place could be worked out (in pack order).
    disabled_ids: list[str] = field(default_factory=list)
    #: Number of ``"disabled": true`` entries (skipped without being checked).
    disabled_count: int = 0

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)


@dataclass
class PackSet:
    """Result of loading several packs."""

    packs: list[Pack]
    drops: list[Drop]  # merged, numbered, sorted by n
    issues: list[PackIssue]

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    @property
    def errors(self) -> list[PackIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[PackIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.packs if p.drops]

    def by_id(self) -> dict[str, Drop]:
        return {d.id: d for d in self.drops}


# ------------------------------------------------------------ normalizers ----

def normalize_spotify(value: str) -> Optional[str]:
    """``spotify:track:<id>`` from a URI, an open.spotify.com track URL or the bare 22-character id
    (the forms the addon's link parser accepts, ``protocol2.spotify_id_from_link``, are accepted
    too). Album, playlist and other non-track links give None."""
    v = value.strip()
    m = _SPOTIFY_URI_RE.match(v) or _SPOTIFY_URL_RE.match(v)
    if m:
        return f"spotify:track:{m.group(1)}"
    if _SPOTIFY_BARE_RE.match(v):
        return f"spotify:track:{v}"
    sid = spotify_id_from_link(v)
    return f"spotify:track:{sid}" if sid else None


def normalize_youtube(value: str) -> Optional[str]:
    """11-char video id from an id or a youtube.com / youtu.be URL (plus every form the addon's
    link parser accepts, ``protocol2.youtube_id_from_link``)."""
    v = value.strip()
    if _YT_ID_RE.match(v):
        return v
    for rx in _YT_URL_RES:
        m = rx.match(v)
        if m:
            return m.group(1)
    return youtube_id_from_link(v)


def derive_pack_id(name: str) -> str:
    """Soundtrack id for a pack without ``"id"`` (as ``tools/gen_sets.py``): accents folded
    (``Ó`` -> ``o``), then the SPEC 3.1 slug, at most 40 characters ("" when nothing is left)."""
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", str(name)) if not unicodedata.combining(ch))
    return slugify(folded)[:MAX_PACK_ID].strip("-")


def is_pack_id(value: Any) -> bool:
    """``value`` is a valid soundtrack id: 1-40 of ``a-z 0-9 -`` with at least one letter or digit."""
    return isinstance(value, str) and _PACK_ID_RE.fullmatch(value) is not None


def _encodable(s: str) -> bool:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def check_tags(value: Any, err: Any, warn: Any) -> list[str]:
    """A list of distinct, non-empty, single-line tags (order kept), as ``tools/gen_sets.py``
    checks them; problems are reported through ``err(msg)`` / ``warn(msg)``."""
    if value is None:
        return []
    if not isinstance(value, list):
        err(f'"tags" must be a list of text, for example ["fitting"], got {type(value).__name__}')
        return []
    out: list[str] = []
    for i, t in enumerate(value):
        if not isinstance(t, str) or not t.strip():
            err(f'"tags"[{i}] must be non-empty text, got {t!r}')
            continue
        if not _encodable(t) or _CONTROL_RE.search(t):
            err(f'"tags"[{i}] must be a single line of plain text, got {t!r}')
            continue
        t = t.strip()
        if t in out:
            warn(f'duplicate tag "{t}" listed once')
            continue
        out.append(t)
    return out


def disabled_drop_id(entry: dict[str, Any]) -> Optional[str]:
    """Best-effort id of a ``"disabled": true`` entry (its place, by the usual precedence), or
    None when the place is missing or invalid. Never raises."""
    def num(v: Any) -> Optional[int]:
        n = _as_int(v)
        return n if n is not None and n > 0 else None

    try:
        inst = num(entry.get("instanceID"))
        area = num(entry.get("areaID"))
        map_id: Optional[int] = None
        zv = entry.get("zone")
        if zv is not None:
            if isinstance(zv, str) and not zv.strip().isdigit():
                z = zones.lookup(zv)
                map_id = z.id if z else None
            else:
                map_id = num(zv)
        sub = entry.get("subzone")
        sub = sub.strip() if isinstance(sub, str) and sub.strip() else None
        if sub is not None and not slugify(sub) and area is None and inst is None:
            return None
        return drop_id_for(mapID=map_id, subzone=sub, areaID=area, instanceID=inst)
    except (ValueError, TypeError):
        return None


def in_game_path(file: str) -> str:
    """WoW path for a file the user copied into ``NeedleDropper_Local\\media``."""
    rel = file.replace("/", "\\").lstrip("\\")
    return IN_GAME_MEDIA_PREFIX + rel


# -------------------------------------------------------------- loading ----

def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _drop_from_entry(entry: dict[str, Any], idx: int, pack: Pack) -> Optional[Drop]:
    """Validate one pack entry; append issues to ``pack.issues``. None on error."""
    hint = str(entry.get("label") or entry.get("subzone") or entry.get("zone") or entry.get("title") or "")[:40]
    errors: list[str] = []
    warnings: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    def warn(msg: str) -> None:
        warnings.append(msg)

    # --- required text fields
    title = entry.get("title")
    artist = entry.get("artist")
    if not isinstance(title, str) or not title.strip():
        err('missing "title" (song title, text)')
    if not isinstance(artist, str) or not artist.strip():
        err('missing "artist" (text)')

    # --- location
    map_id: Optional[int] = None
    zone_name: Optional[str] = None
    if "zone" in entry and entry["zone"] is not None:
        zv = entry["zone"]
        zi = _as_int(zv) if not isinstance(zv, str) or zv.strip().isdigit() else None
        if zi is not None:
            if zi <= 0:
                err(f'"zone" must be a positive uiMapID, got {zv!r}')
            else:
                map_id = zi
                z = zones.lookup(zi)
                if z:
                    zone_name = z.name
                else:
                    warn(f"zone {zi} is not in the known zone table (fine if it is a valid uiMapID; "
                         "check with /needle where)")
        elif isinstance(zv, str):
            z = zones.lookup(zv)
            if z:
                map_id, zone_name = z.id, z.name
            else:
                sugg = zones.suggest(zv)
                extra = f" -- did you mean {', '.join(repr(s) for s in sugg)}?" if sugg else \
                    " (run `needledropper zones` for the list, or use the uiMapID number)"
                err(f"unknown zone {zv!r}{extra}")
        else:
            err(f'"zone" must be a zone name or uiMapID number, got {type(zv).__name__}')

    subzone = entry.get("subzone")
    if subzone is not None and (not isinstance(subzone, str) or not subzone.strip()):
        err('"subzone" must be a non-empty name')
        subzone = None
    elif isinstance(subzone, str):
        subzone = subzone.strip()

    area_id: Optional[int] = None
    if entry.get("areaID") is not None:
        area_id = _as_int(entry["areaID"])
        if area_id is None or area_id <= 0:
            err(f'"areaID" must be a positive integer, got {entry["areaID"]!r}')
            area_id = None

    instance_id: Optional[int] = None
    if entry.get("instanceID") is not None:
        instance_id = _as_int(entry["instanceID"])
        if instance_id is None or instance_id <= 0:
            err(f'"instanceID" must be a positive integer, got {entry["instanceID"]!r}')
            instance_id = None

    if map_id is None and subzone is None and area_id is None and instance_id is None and "zone" not in entry:
        err('needs a location: "zone" (name or uiMapID), optionally "subzone"/"areaID", or "instanceID"')
    if subzone and map_id is None and area_id is None and instance_id is None and not errors:
        err(f'subzone {subzone!r} needs a "zone" (or an "areaID")')

    # --- optional numeric fields
    nums: dict[str, int] = {}
    for key in ("startMs", "durationMs"):
        if entry.get(key) is not None:
            v = _as_int(entry[key])
            if v is None or v < 0:
                err(f'"{key}" must be a whole number of milliseconds >= 0, got {entry[key]!r}')
            else:
                nums[key] = v
    volume: Optional[int] = None
    if entry.get("volume") is not None:
        v = _as_int(entry["volume"])
        if v is None or not 0 <= v <= 100:
            err(f'"volume" must be 0..100, got {entry["volume"]!r}')
        else:
            volume = v

    # --- sources
    spotify = None
    if entry.get("spotify") not in (None, ""):
        if not isinstance(entry["spotify"], str) or not (spotify := normalize_spotify(entry["spotify"])):
            err(f'"spotify" must be "spotify:track:<22-char id>" or an open.spotify.com/track/ link, '
                f'got {entry["spotify"]!r}')
    youtube = None
    if entry.get("youtube") not in (None, ""):
        if not isinstance(entry["youtube"], str) or not (youtube := normalize_youtube(entry["youtube"])):
            err(f'"youtube" must be an 11-character video id or a YouTube link, got {entry["youtube"]!r}')
    apple = None
    if entry.get("appleMusic") not in (None, ""):
        av = entry["appleMusic"]
        if not isinstance(av, str) or not re.match(r"^https://", av.strip()):
            err(f'"appleMusic" must be an https:// link (https://music.apple.com/...), got {av!r}')
        else:
            apple = av.strip()
            if "music.apple.com" not in apple:
                warn('"appleMusic" is not a music.apple.com link')
    file = None
    if entry.get("file") not in (None, ""):
        if not isinstance(entry["file"], str):
            err('"file" must be a file name/path (text)')
        else:
            file = entry["file"].strip()
    in_game: Optional[str] = None
    ig = entry.get("inGameFile")
    if ig not in (None, False):
        if ig is not True:
            err('"inGameFile" must be true or false')
        elif not file:
            err('"inGameFile": true needs a "file" (copied into Interface\\AddOns\\NeedleDropper_Local\\media)')
        else:
            if os.path.isabs(file) or PureWindowsPath(file).is_absolute() or PureWindowsPath(file).drive:
                name = PureWindowsPath(file).name
                warn(f'"file" is an absolute path; in game it is expected at '
                     f'NeedleDropper_Local\\media\\{name}')
                in_game = in_game_path(name)
            else:
                in_game = in_game_path(file)
            if not file.lower().endswith((".mp3", ".ogg")):
                warn("in-game playback only supports .mp3 and .ogg files")

    for key in ("label", "note"):
        if entry.get(key) is not None and not isinstance(entry[key], str):
            err(f'"{key}" must be text')
    tags = check_tags(entry.get("tags"), err, warn)

    unknown = sorted(set(entry) - KNOWN_DROP_KEYS)
    if unknown:
        warn(f"unknown key(s) {', '.join(repr(k) for k in unknown)} (kept, but ignored)")
    drop: Optional[Drop] = None
    if not errors:
        try:
            did = drop_id_for(mapID=map_id, subzone=subzone, areaID=area_id, instanceID=instance_id)
        except ValueError as exc:
            err(str(exc))
        else:
            if entry.get("id") not in (None, "") and entry["id"] != did:
                warn(f'"id" {entry["id"]!r} ignored; derived id is {did!r}')
            if instance_id is not None:
                kind = "instance"
            elif area_id is not None or subzone:
                kind = "subzone"
            else:
                kind = "zone"
            label = (entry.get("label") or "").strip()
            if not label:
                if kind == "instance":
                    label = f"Instance {instance_id}"
                    warn('instance drops should have a "label" (shown on the title card)')
                elif kind == "subzone":
                    area = zones.lookup_area(area_id) if area_id is not None else None
                    label = subzone or (area.name if area else "") or f"Area {area_id}"
                    if map_id is None and area is not None:
                        map_id = area.zone
                else:
                    label = zone_name or f"Zone {map_id}"
            if kind == "subzone" and not subzone and area_id is not None:
                area = zones.lookup_area(area_id)
                if area is not None:
                    subzone = area.name
                    if map_id is None:
                        map_id = area.zone
            extra = {k: entry[k] for k in unknown}
            if tags:
                extra["tags"] = tags
            drop = Drop(
                n=0, id=did, kind=kind, title=str(title).strip(), artist=str(artist).strip(), label=label,
                mapID=map_id, areaID=area_id, subzone=subzone, instanceID=instance_id,
                durationMs=nums.get("durationMs", 0), startMs=nums.get("startMs", 0), volume=volume,
                spotify=spotify, youtube=youtube, appleMusic=apple, file=file, inGameFile=in_game,
                note=str(entry.get("note") or ""), pack=pack.name, extra=extra,
            )
    src = str(pack.path) if pack.path else ""
    for m in errors:
        pack.issues.append(PackIssue("error", pack.name, m, idx, hint, src))
    for m in warnings:
        pack.issues.append(PackIssue("warning", pack.name, m, idx, hint, src))
    return drop


def parse_drop(entry: Any, index: int = 0, *, pack_name: str = "pack") -> tuple[Optional[Drop], list[PackIssue]]:
    """Validate one drop entry on its own (the rules :func:`parse_pack` applies to each drop).

    Returns ``(drop or None, issues)``; never raises. A ``"disabled": true`` entry gives
    ``(None, [])`` (turned-off entries are not checked).
    """
    pack = Pack(name=pack_name, path=None)
    if not isinstance(entry, dict):
        return None, [PackIssue("error", pack_name, "each drop must be a JSON object", index)]
    disabled = entry.get("disabled")
    if disabled is not None and not isinstance(disabled, bool):
        return None, [PackIssue("error", pack_name, f'"disabled" must be true or false, got {disabled!r}', index)]
    if disabled:
        return None, []
    return _drop_from_entry(entry, index, pack), pack.issues


def parse_pack(data: Any, *, name_hint: str = "pack", path: Optional[Path] = None) -> Pack:
    """Validate already-parsed pack JSON. Never raises; see ``Pack.issues``."""
    pack = Pack(name=name_hint, path=path)
    src = str(path) if path else ""

    def top(level: str, msg: str) -> None:
        pack.issues.append(PackIssue(level, pack.name, msg, None, "", src))

    if not isinstance(data, dict):
        top("error", "top level must be a JSON object with \"format\", \"name\" and \"drops\"")
        return pack
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        pack.name = name.strip()
    else:
        top("warning", f'missing "name"; using {name_hint!r}')
    fmt = data.get("format")
    if fmt is None:
        top("warning", f'missing "format": "{PACK_FORMAT}"')
    elif fmt != PACK_FORMAT:
        top("error", f'"format" must be "{PACK_FORMAT}", got {fmt!r}')
        return pack
    ver = data.get("version", PACK_VERSION)
    if _as_int(ver) != PACK_VERSION:
        top("error", f'unsupported pack "version" {ver!r} (this companion reads version {PACK_VERSION})')
        return pack
    pack.author = str(data.get("author") or "")
    pack.description = str(data.get("description") or "")
    _parse_share_fields(data, pack, top)
    drops = data.get("drops")
    if not isinstance(drops, list):
        top("error", '"drops" must be a list')
        return pack
    if not drops:
        top("warning", "pack has no drops")
    seen: dict[str, int] = {}
    for idx, entry in enumerate(drops):
        if not isinstance(entry, dict):
            pack.issues.append(PackIssue("error", pack.name, "each drop must be a JSON object", idx, "", src))
            continue
        disabled = entry.get("disabled")
        if disabled is not None and not isinstance(disabled, bool):
            hint = str(entry.get("label") or entry.get("subzone") or entry.get("zone") or entry.get("title") or "")
            pack.issues.append(PackIssue("error", pack.name, f'"disabled" must be true or false, got {disabled!r}',
                                         idx, hint[:40], src))
            continue
        if disabled:
            # Skipped without further checks (a bare place that switches a drop off, or a draft).
            pack.disabled_count += 1
            did = disabled_drop_id(entry)
            if did is not None and did not in pack.disabled_ids:
                pack.disabled_ids.append(did)
            continue
        d = _drop_from_entry(entry, idx, pack)
        if d is None:
            continue
        if d.id == "test":
            continue
        if d.id in seen:
            pack.issues.append(PackIssue(
                "warning", pack.name, f"duplicate drop id {d.id!r} (also drops[{seen[d.id]}]); the later one wins",
                idx, d.display_label, src))
            pack.drops = [x for x in pack.drops if x.id != d.id]
        seen[d.id] = idx
        pack.drops.append(d)
    return pack


def _parse_share_fields(data: dict[str, Any], pack: Pack, top: Any) -> None:
    """The top-level share-code fields: ``id``, ``tags``, ``homepage``, ``revision``."""
    raw_id = data.get("id")
    if raw_id in (None, ""):
        pack.id = derive_pack_id(pack.name)
    elif is_pack_id(raw_id):
        pack.id = raw_id
    else:
        suggestion = derive_pack_id(raw_id if isinstance(raw_id, str) else pack.name) or "my-soundtrack"
        top("error", f'"id" must be 1-{MAX_PACK_ID} lowercase letters, digits and dashes '
                     f'(for example "{suggestion}"), got {raw_id!r}')
    pack.tags = check_tags(data.get("tags"), lambda m: top("error", m), lambda m: top("warning", m))
    homepage = data.get("homepage")
    if homepage not in (None, ""):
        if isinstance(homepage, str) and _URL_RE.fullmatch(homepage.strip()):
            pack.homepage = homepage.strip()
        else:
            top("error", f'"homepage" must be an http:// or https:// link, got {homepage!r}')
    rev = data.get("revision")
    if rev is not None:
        n = rev if isinstance(rev, int) and not isinstance(rev, bool) else \
            (int(rev) if isinstance(rev, float) and rev.is_integer() else None)
        if n is None or n < 0:
            top("error", f'"revision" must be a whole number >= 0, got {rev!r}')
        else:
            pack.revision = n


def load_pack(path: str | os.PathLike[str]) -> Pack:
    """Read and validate one pack file. Never raises; see ``Pack.issues``."""
    p = Path(path)
    stem = p.stem
    try:
        text = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        pack = Pack(name=stem, path=p)
        pack.issues.append(PackIssue("error", stem, f"pack file not found: {p}", source=str(p)))
        return pack
    except OSError as exc:
        pack = Pack(name=stem, path=p)
        pack.issues.append(PackIssue("error", stem, f"cannot read {p}: {exc}", source=str(p)))
        return pack
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        pack = Pack(name=stem, path=p)
        pack.issues.append(PackIssue(
            "error", stem, f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg} "
                           "(check for a missing comma or a trailing comma)", source=str(p)))
        return pack
    return parse_pack(data, name_hint=stem, path=p)


# -------------------------------------------------------------- merging ----

def assign_numbers(drops: Iterable[Drop]) -> list[Drop]:
    """Number drops 1..N by plain string sort of their ids (SPEC 3.2). Returns new objects."""
    ordered = sorted(drops, key=lambda d: d.id)
    if len(ordered) > MAX_DROPS:
        raise PackError([PackIssue("error", "packs", f"too many drops ({len(ordered)} > {MAX_DROPS})")])
    return [replace(d, n=i) for i, d in enumerate(ordered, start=1)]


def load_resolved_cache(path: Optional[str | os.PathLike[str]]) -> dict[str, dict[str, Any]]:
    """``{dropId: {spotify, durationMs, artUrl, ...}}``; {} if absent or unreadable."""
    if not path:
        return {}
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _loose(s: Any) -> str:
    return zones.normalize(str(s or ""))


def resolved_entry_applies(drop: Drop, entry: dict[str, Any]) -> bool:
    """Is a cache entry still about this drop's song?

    Entries record the pack's title/artist at resolve time (``queryTitle``/
    ``queryArtist``); if the pack changed the song since, the entry is stale.
    If the pack pins a Spotify track, the entry must be for that track.
    """
    uri = entry.get("spotify") or entry.get("uri")
    norm = normalize_spotify(uri) if isinstance(uri, str) else None
    if drop.spotify:
        return norm == drop.spotify
    if not norm:
        return False
    qt, qa = entry.get("queryTitle"), entry.get("queryArtist")
    if qt is not None and _loose(qt) != _loose(drop.title):
        return False
    if qa is not None and _loose(qa) != _loose(drop.artist):
        return False
    return True


def apply_resolved(drop: Drop, entry: dict[str, Any]) -> Drop:
    """Fill fields the pack left empty from a resolution-cache entry (pack values win).

    ``durationMs`` is the play length from ``startMs``; when the entry carries the
    full ``trackDurationMs`` it is recomputed against the pack's current ``startMs``.
    """
    if not resolved_entry_applies(drop, entry):
        return drop
    changes: dict[str, Any] = {}
    uri = entry.get("spotify") or entry.get("uri")
    if not drop.spotify and isinstance(uri, str):
        norm = normalize_spotify(uri)
        if norm:
            changes["spotify"] = norm
    track_ms = _as_int(entry.get("trackDurationMs")) or 0
    dur = _as_int(entry.get("durationMs")) or 0
    if track_ms > 0:
        dur = max(0, track_ms - int(drop.startMs or 0))
    if not drop.durationMs and dur > 0:
        changes["durationMs"] = dur
    extra = dict(drop.extra)
    art = entry.get("artUrl")
    if isinstance(art, str) and art and "artUrl" not in extra:
        extra["artUrl"] = art
    extra["resolved"] = dict(entry)
    changes["extra"] = extra
    return replace(drop, **changes)


def merge_packs(packs: Sequence[Pack]) -> list[Drop]:
    """Merge drops; a later pack replaces an earlier pack's drop with the same id, and a later
    pack's ``"disabled": true`` entry removes an earlier pack's drop for that place."""
    merged: dict[str, Drop] = {}
    for pack in packs:
        own = {d.id for d in pack.drops}
        for did in pack.disabled_ids:
            if did not in own:
                merged.pop(did, None)
        for d in pack.drops:
            merged[d.id] = d
    return list(merged.values())


def load_packs_detailed(paths: Iterable[str | os.PathLike[str]], *,
                        resolved_cache: Optional[str | os.PathLike[str] | dict[str, dict[str, Any]]] = None
                        ) -> PackSet:
    """Load, validate, merge, enrich and number. Never raises; inspect ``.ok``/``.issues``."""
    packs = [load_pack(p) for p in paths]
    issues = [i for p in packs for i in p.issues]
    merged = merge_packs(packs)
    cache = resolved_cache if isinstance(resolved_cache, dict) else load_resolved_cache(resolved_cache)
    if cache:
        merged = [apply_resolved(d, cache[d.id]) if d.id in cache else d for d in merged]
    try:
        numbered = assign_numbers(merged)
    except PackError as exc:
        issues.extend(exc.issues)
        numbered = []
    return PackSet(packs=packs, drops=numbered, issues=issues)


def load_packs(paths: Iterable[str | os.PathLike[str]], *,
               resolved_cache: Optional[str | os.PathLike[str] | dict[str, dict[str, Any]]] = None,
               strict: bool = True) -> list[Drop]:
    """Merged, numbered drops. ``strict`` raises :class:`PackError` on any error."""
    ps = load_packs_detailed(paths, resolved_cache=resolved_cache)
    if strict and not ps.ok:
        raise PackError(ps.issues)
    return ps.drops


def load_from_config(cfg: dict[str, Any], paths: Any) -> PackSet:
    """Load the packs named in config, with the app's resolution cache."""
    from .config import pack_paths_from_config

    return load_packs_detailed(pack_paths_from_config(cfg), resolved_cache=paths.resolved_cache)


def validate_report(ps: PackSet) -> tuple[list[str], bool]:
    """Human-readable lines for ``needledropper validate`` and an ok flag."""
    lines: list[str] = []
    for pack in ps.packs:
        n_err = sum(1 for i in pack.issues if i.level == "error")
        n_warn = sum(1 for i in pack.issues if i.level == "warning")
        status = "OK" if n_err == 0 else "FAILED"
        loc = f"  [{pack.path}]" if pack.path else ""
        off = f" (+{pack.disabled_count} turned off)" if pack.disabled_count else ""
        lines.append(f'{status:6}  "{pack.name}": {len(pack.drops)} drop(s){off}, {n_err} error(s), '
                     f"{n_warn} warning(s){loc}")
        for i in pack.issues:
            tag = "ERROR" if i.level == "error" else "warn "
            lines.append(f"    {tag} {i}")
    kinds: dict[str, int] = {}
    for d in ps.drops:
        kinds[d.kind] = kinds.get(d.kind, 0) + 1
    srcs = {
        "spotify": sum(1 for d in ps.drops if d.spotify),
        "youtube": sum(1 for d in ps.drops if d.youtube),
        "appleMusic": sum(1 for d in ps.drops if d.appleMusic),
        "file": sum(1 for d in ps.drops if d.file),
        "inGame": sum(1 for d in ps.drops if d.inGameFile),
    }
    kind_s = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) or "none"
    src_s = ", ".join(f"{v} {k}" for k, v in srcs.items() if v) or "no sources yet"
    lines.append(f"Total: {len(ps.drops)} drop(s) after merge ({kind_s}); sources: {src_s}.")
    unresolved = sum(1 for d in ps.drops if not d.spotify and not d.youtube and not d.appleMusic and not d.file)
    if unresolved:
        lines.append(f"{unresolved} drop(s) have no source yet -- `needledropper resolve` can find them on Spotify.")
    ok = ps.ok
    lines.append("Packs are valid." if ok else f"{len(ps.errors)} error(s) -- fix them and run validate again.")
    return lines, ok


def find_drop(drops: Sequence[Drop], query: str) -> Optional[Drop]:
    """Find a drop by id, zone name/uiMapID, label or subzone (for ``test-drop``)."""
    q = query.strip()
    by_id = {d.id: d for d in drops}
    if q in by_id:
        return by_id[q]
    ql = q.lower()
    for d in drops:
        if d.id.lower() == ql:
            return d
    z = zones.lookup(q)
    if z is not None and f"zone:{z.id}" in by_id:
        return by_id[f"zone:{z.id}"]
    key = zones.normalize(q)
    slug = slugify(q)
    for d in drops:
        if zones.normalize(d.display_label) == key or (d.subzone and slugify(d.subzone) == slug):
            return d
    for d in drops:
        if key and key in zones.normalize(d.display_label):
            return d
    return None
