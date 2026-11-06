"""Soundtrack share codes: ``!NeedleDropper:1!<Base64(zlib(JSON))>!`` (docs/DESIGN-v1.2.md section 5).

A share code carries one soundtrack (a ``needledropper-pack`` v1 JSON object, the same format as
``packs/*.json``) as text a player can paste into Discord and back into the game's Import dialog.
This module is the Python half of that contract (the addon's ``Share.lua`` is the other): it is
used by ``needledropper packs encode / decode / validate`` and by the one-time migration of v1.1
companion packs (``packs convert-legacy``). Nothing here touches the network or the disk.

Grammar (DESIGN 5.1)::

    code    = "!NeedleDropper:" container "!" body "!"     ; container version, currently 1
    body    = Base64 (RFC 4648 section 4, standard alphabet, padded), no line breaks
    payload = Base64-decoded bytes = zlib stream (RFC 1950) of UTF-8 JSON (RFC 8259)

Readers (:func:`decode`) accept the code anywhere in the text (Discord fences, chatter), whitespace
and line breaks anywhere in the body, U+200B / U+FEFF (removed) and U+00A0 (a space), the URL-safe
alphabet, missing or partial padding, a missing final ``!`` (a failure then reads "cut off"), and an
uncompressed payload whose first byte is ``{`` (never written; keeps the reader testable without
zlib). Every failure raises :class:`ShareCodeError` whose ``reason`` is one of the strings the addon
uses: ``notcode``, ``foreign:<Name>``, ``newer``, ``cutoff``, ``damaged``, ``toobig``, ``invalid``.

Limits (DESIGN 5.5, the same in Lua and Python): pasted text <= 65,536 characters, decoded payload
<= 49,152 bytes, inflated JSON <= 262,144 bytes (a zlib bomb stops inflating there), JSON nesting
<= 6 levels. NaN, Infinity, numbers too large for a double and unpaired surrogate escapes are
"damaged" (the addon's strict JSON reader rejects them too). A payload that decodes is then
validated like a pack file (:func:`needledropper.packs.parse_pack`) plus the share-code rules
(:func:`validate`): 1-300 drops, each place once, text lengths, tags, numeric place ids, plain
in-game file names; unknown keys are dropped with a warning.

Writers (:func:`encode`) emit the pack with sorted keys, compact separators and raw UTF-8, zlib
level 9 and padded standard Base64. :func:`pack_for_sharing` turns a pack file into that writer
form first (numeric zone ids, ``spotify:track:<id>``, unfinished drops left out, ...).
"""
from __future__ import annotations

import base64
import binascii
import copy
import json
import math
import re
import zlib
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any, Mapping, Optional, Sequence

from . import packs as _packs
from . import zones

__all__ = [
    "PREFIX", "CONTAINER_VERSION", "CODE_RE", "REASONS", "FOREIGN_ANYWHERE", "FOREIGN_AT_START",
    "MAX_TEXT_CHARS", "MAX_PAYLOAD_BYTES", "MAX_JSON_BYTES", "MAX_DEPTH", "MAX_DROPS",
    "ShareCodeError", "Decoded", "encode", "decode", "decode_detailed", "validate", "pack_for_sharing",
    "foreign_prefix", "find_code", "ints_from_integral_floats", "describe_reason", "is_bare_file_name",
]

PREFIX = "!NeedleDropper:1!"
CONTAINER_VERSION = 1
#: The code inside arbitrary text: version digits, the body (both Base64 alphabets, padding and
#: whitespace) and the optional terminating ``!``. ASCII classes only, like Lua's patterns.
CODE_RE = re.compile(r"!NeedleDropper:([0-9]+)!([A-Za-z0-9+/=_\-\s]*)(!?)", re.ASCII)

#: Reason kinds (``foreign`` is reported as ``foreign:<Name>``).
REASONS = ("notcode", "foreign", "newer", "cutoff", "damaged", "toobig", "invalid")

#: Other addons' export strings, recognised anywhere in the pasted text (DESIGN 5.1).
FOREIGN_ANYWHERE: tuple[tuple[str, str], ...] = (
    ("!WA:", "WeakAuras"), ("!E1!", "ElvUI"), ("!E2!", "ElvUI"), ("!PLATER:", "Plater"), ("!~MDT2~", "MDT"),
    ("D!ProfileV2-", "Details"), ("!CELL:", "Cell"),
)
#: ... and these only at the start of the trimmed text (anywhere they would match ordinary text).
FOREIGN_AT_START: tuple[tuple[str, str], ...] = (("BW2:", "BigWigs"), ("BWB1:", "BigWigs"), ("1|", "Blizzard"))

MAX_TEXT_CHARS = 65536
MAX_PAYLOAD_BYTES = 49152
MAX_JSON_BYTES = 262144
MAX_DEPTH = 6
MAX_DROPS = 300
MAX_TAGS = 8
MAX_TAG_BYTES = 24
MAX_FILE_BYTES = 100
MAX_INT = 2 ** 31 - 1
_MAX_EXACT_INT = 2 ** 53

#: Text caps in UTF-8 bytes (DESIGN 5.5).
TOP_TEXT_CAPS = {"name": 64, "author": 64, "description": 1000, "homepage": 200}
DROP_TEXT_CAPS = {"artist": 200, "title": 200, "subzone": 100, "label": 100, "note": 500}
_MULTILINE = frozenset(("description", "note"))  # the only texts that keep "\n"

_FILE_RE = re.compile(r"[A-Za-z0-9 ._\-()]+\.(?:mp3|ogg)", re.IGNORECASE | re.ASCII)
_SPOTIFY_URI_RE = re.compile(r"spotify:track:([0-9A-Za-z]{22})", re.ASCII)
_ZW_TABLE = str.maketrans({"​": "", "﻿": "", " ": " "})
_STRIP_WS = " \t\r\n"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_KEEP_NL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_WS_RE = re.compile(r"\s", re.ASCII)
_WS_EQ_RE = re.compile(r"[\s=]", re.ASCII)

_MESSAGES = {
    "notcode": "This is not a NeedleDropper soundtrack code.",
    "newer": "This code was made with a newer NeedleDropper: update the addon.",
    "cutoff": "The code was cut off: copy all of it, up to and including the ! at the end.",
    "damaged": "The code is damaged: copy it again from where it was shared.",
    "toobig": "This code is too big to be a NeedleDropper soundtrack.",
    "invalid": "This soundtrack has problems and cannot be imported.",
}


class ShareCodeError(ValueError):
    """A share code could not be decoded (or a pack could not be encoded).

    ``reason`` is the addon's reason string (``notcode``, ``foreign:<Name>``, ``newer``, ``cutoff``,
    ``damaged``, ``toobig``, ``invalid``); ``kind`` is its part before ``:``; ``issues`` lists the
    validation problems behind ``invalid``; ``detail`` is extra context for logs. ``str(exc)`` is a
    plain-English sentence for the user.
    """

    def __init__(self, reason: str, detail: str = "", *, issues: Sequence[str] = ()) -> None:
        self.reason = reason
        self.kind = reason.split(":", 1)[0]
        self.detail = detail
        self.issues = list(issues)
        text = describe_reason(reason)
        if self.issues:
            more = f" (+{len(self.issues) - 3} more)" if len(self.issues) > 3 else ""
            text += " " + "; ".join(self.issues[:3]) + more
        elif detail:
            text += f" ({detail})"
        super().__init__(text)

    @property
    def foreign_name(self) -> Optional[str]:
        """The other addon's name for a ``foreign:<Name>`` reason, else None."""
        return self.reason.split(":", 1)[1] if self.kind == "foreign" and ":" in self.reason else None


def describe_reason(reason: str) -> str:
    """Plain-English text for a reason string (``foreign:WeakAuras`` -> "This is a WeakAuras string, ...")."""
    kind, _, name = reason.partition(":")
    if kind == "foreign":
        return f"This is a {name or 'different addon'} string, not a NeedleDropper soundtrack."
    return _MESSAGES.get(kind, reason)


@dataclass(frozen=True)
class Decoded:
    """Result of :func:`decode_detailed`.

    ``pack`` is the decoded JSON object (integral floats as ints, JSON nulls left out, as the addon's
    JSON reader does); ``normalized`` is the same soundtrack in writer form after validation (what an
    import keeps: unknown keys and unusable fields dropped); ``warnings`` lists what validation
    dropped or noticed; ``compressed`` is False for the uncompressed ``{`` payload; ``length`` is the
    number of characters of the code itself.
    """

    pack: dict[str, Any]
    normalized: dict[str, Any]
    warnings: tuple[str, ...] = ()
    compressed: bool = True
    length: int = 0


# ------------------------------------------------------------------ helpers --

def foreign_prefix(text: Any) -> Optional[str]:
    """``foreign:<Name>`` when ``text`` holds another addon's export string, else None."""
    if not isinstance(text, str):
        return None
    for needle, name in FOREIGN_ANYWHERE:
        if needle in text:
            return f"foreign:{name}"
    trimmed = text.strip(_STRIP_WS)
    for start, name in FOREIGN_AT_START:
        if trimmed.startswith(start):
            return f"foreign:{name}"
    return None


def find_code(text: Any) -> Optional[str]:
    """The share code inside ``text`` (prefix to the final ``!``, whitespace removed), or None.
    Only locates the code; :func:`decode` checks it."""
    if not isinstance(text, str):
        return None
    m = CODE_RE.search(text.translate(_ZW_TABLE))
    if not m:
        return None
    return f"!NeedleDropper:{m.group(1)}!{_WS_RE.sub('', m.group(2))}{m.group(3)}"


def ints_from_integral_floats(value: Any) -> Any:
    """A copy of a JSON value with integral floats (``1429.0``, ``1e3``) as ints, as Lua numbers are
    (one number type); object members that are JSON null are left out, as the addon's reader does."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and abs(value) <= _MAX_EXACT_INT:
            return int(value)
        return value
    if isinstance(value, dict):
        return {k: ints_from_integral_floats(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [ints_from_integral_floats(v) for v in value]
    return value


def is_bare_file_name(name: Any) -> bool:
    """An in-game audio file name the addon accepts (DESIGN 5.5): letters, digits, space and
    ``._-()``, ending in ``.mp3`` or ``.ogg``, at most 100 bytes, no path parts and no ``..``."""
    return (isinstance(name, str) and len(name.encode("utf-8", "surrogatepass")) <= MAX_FILE_BYTES
            and _FILE_RE.fullmatch(name) is not None and ".." not in name)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON number")


def _finite_float(text: str) -> float:
    v = float(text)
    if not math.isfinite(v):
        raise ValueError(f"number out of range: {text[:40]}")
    return v


def _finite_int(text: str) -> int:
    if not math.isfinite(float(text)):  # float() of a huge numeral is inf, never an error
        raise ValueError("number out of range (too large for a double)")
    return int(text)


def _check_structure(value: Any) -> None:
    """Nesting <= 6 levels (the top level counts as 1) and only UTF-8-encodable strings (no
    unpaired surrogate escapes), like the addon's JSON reader. Raises ValueError. Iterative."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        v, depth = stack.pop()
        if isinstance(v, str):
            v.encode("utf-8")  # UnicodeEncodeError is a ValueError
        elif isinstance(v, (dict, list)):
            if depth > MAX_DEPTH:
                raise ValueError(f"nesting deeper than {MAX_DEPTH} levels")
            if isinstance(v, dict):
                for k, item in v.items():
                    k.encode("utf-8")
                    stack.append((item, depth + 1))
            else:
                stack.extend((item, depth + 1) for item in v)


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8", "surrogatepass"))


def _encodable(s: str) -> bool:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# ------------------------------------------------------------------- encode --

def encode(pack: Mapping[str, Any]) -> str:
    """The share code of ``pack`` (a needledropper-pack object, normally from :func:`pack_for_sharing`).

    Deterministic: sorted keys, compact separators, raw UTF-8, zlib level 9, padded standard
    Base64 (DESIGN 5.4). Raises :class:`ShareCodeError` -- ``invalid`` for values JSON cannot carry
    (NaN, text that is not valid UTF-8, non-JSON types), ``toobig`` when readers would refuse it.
    """
    try:
        text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
        raw = text.encode("utf-8")
    except (TypeError, ValueError) as exc:  # NaN/inf, non-JSON types, unpaired surrogates
        raise ShareCodeError("invalid", f"cannot write the pack as JSON: {exc}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise ShareCodeError("toobig", f"the pack is {len(raw):,} bytes of JSON (at most {MAX_JSON_BYTES:,})")
    blob = zlib.compress(raw, 9)
    if len(blob) > MAX_PAYLOAD_BYTES:
        raise ShareCodeError("toobig", f"the pack compresses to {len(blob):,} bytes (at most {MAX_PAYLOAD_BYTES:,})")
    return PREFIX + base64.b64encode(blob).decode("ascii") + "!"


# ------------------------------------------------------------------- decode --

def _payload(text: Any) -> tuple[Any, bool, int]:
    """DESIGN 5.4 up to the JSON value: text -> (value, compressed, code length)."""
    if not isinstance(text, str):
        raise ShareCodeError("notcode", "not text")
    if len(text) > MAX_TEXT_CHARS:
        raise ShareCodeError("toobig", f"{len(text):,} characters pasted (at most {MAX_TEXT_CHARS:,})")
    text = text.translate(_ZW_TABLE)
    m = CODE_RE.search(text)
    if not m:
        raise ShareCodeError(foreign_prefix(text) or "notcode")
    digits = m.group(1).lstrip("0") or "0"
    version = int(digits) if len(digits) <= 9 else MAX_INT * 2  # a huge numeral is just "newer"
    if version != CONTAINER_VERSION:
        raise ShareCodeError("newer" if version > CONTAINER_VERSION else "damaged",
                             f"container version {m.group(1)[:12]}")
    cut = m.group(3) == ""
    body = _WS_EQ_RE.sub("", m.group(2)).replace("-", "+").replace("_", "/")
    compressed = True
    try:
        blob = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
        if base64.b64encode(blob).decode("ascii").rstrip("=") != body:
            # unused low bits of the last character are not zero: the addon's Base64 rejects that too
            raise ShareCodeError("cutoff" if cut else "damaged", "non-canonical Base64 ending")
        if len(blob) > MAX_PAYLOAD_BYTES:
            raise ShareCodeError("toobig", f"{len(blob):,} bytes of data (at most {MAX_PAYLOAD_BYTES:,})")
        if blob[:1] == b"{":
            raw = blob
            compressed = False
        else:
            d = zlib.decompressobj()
            raw = d.decompress(blob, MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:  # before the eof check: a bomb is "too big", not "cut off"
                raise ShareCodeError("toobig", f"the data inflates past {MAX_JSON_BYTES:,} bytes")
            if not d.eof:
                raise ShareCodeError("cutoff" if cut else "damaged", "the compressed data ends early")
        json_text = raw.decode("utf-8")
        if json_text.startswith("﻿"):
            json_text = json_text[1:]  # the addon's reader skips a byte order mark too
        value = json.loads(json_text, parse_constant=_reject_constant, parse_float=_finite_float,
                           parse_int=_finite_int)
        _check_structure(value)
    except ShareCodeError:
        raise
    except (binascii.Error, zlib.error, UnicodeError, ValueError, RecursionError) as exc:
        raise ShareCodeError("cutoff" if cut else "damaged", f"{exc.__class__.__name__}: {exc}"[:200]) from exc
    return ints_from_integral_floats(value), compressed, m.end() - m.start()


def decode_detailed(text: str) -> Decoded:
    """Decode and validate the share code found anywhere in ``text`` (see the module docs).
    Raises :class:`ShareCodeError` with the addon's reason string."""
    value, compressed, length = _payload(text)
    if not isinstance(value, dict):
        raise ShareCodeError("damaged", "the data is not a JSON object")
    normalized, warnings = validate(value)
    return Decoded(pack=value, normalized=normalized, warnings=tuple(warnings), compressed=compressed,
                   length=length)


def decode(text: str) -> dict[str, Any]:
    """The pack object of the share code in ``text`` (integral floats as ints), after validating it
    like a pack file plus the share-code rules. Raises :class:`ShareCodeError`."""
    return decode_detailed(text).pack


# ----------------------------------------------------------------- validate --

@dataclass
class _Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean(value: str, key: str) -> str:
    """Control characters removed (``\\n`` kept in description and note), then trimmed."""
    rx = _CONTROL_KEEP_NL_RE if key in _MULTILINE else _CONTROL_RE
    return rx.sub("", value).strip()


def _texts(entry: Mapping[str, Any], caps: Mapping[str, int], where: str, rep: _Report) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, cap in caps.items():
        v = entry.get(key)
        if v is None:
            continue
        if not isinstance(v, str):
            rep.errors.append(f'{where}"{key}" must be text, got {type(v).__name__}')
            continue
        if not _encodable(v):
            rep.errors.append(f'{where}"{key}" contains invalid text (an unpaired surrogate escape)')
            continue
        v = _clean(v, key)
        if _byte_len(v) > cap:
            rep.errors.append(f'{where}"{key}" is {_byte_len(v)} bytes long (at most {cap})')
            continue
        if v:
            out[key] = v
    return out


def _place_numbers(entry: Mapping[str, Any], where: str, rep: _Report) -> dict[str, int]:
    """``zone`` / ``areaID`` / ``instanceID`` of a share-code drop: whole numbers 1..2^31-1 (zone
    names are a pack-file convenience that :func:`pack_for_sharing` converts)."""
    out: dict[str, int] = {}
    for key in ("zone", "areaID", "instanceID"):
        v = entry.get(key)
        if v is None:
            continue
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_INT:
            what = "a uiMapID number" if key == "zone" else "a whole number"
            rep.errors.append(f'{where}"{key}" must be {what} from 1 to {MAX_INT}, got {v!r}')
            continue
        out[key] = v
    return out


def _tags(value: Any, where: str, rep: _Report) -> list[str]:
    """Clean tags plus the share-code caps (8 tags, 24 bytes each). The basic rules (text, one line,
    no duplicates) are reported by ``packs.parse_pack`` already, so they are not repeated here."""
    if value is None:
        return []
    tags = _packs.check_tags(value, lambda m: None, lambda m: None)
    if len(tags) > MAX_TAGS:
        rep.errors.append(f"{where}at most {MAX_TAGS} tags, got {len(tags)}")
    for t in tags:
        if _byte_len(t) > MAX_TAG_BYTES:
            rep.errors.append(f'{where}tag "{t}" is longer than {MAX_TAG_BYTES} bytes')
    return tags


def _file(entry: Mapping[str, Any], where: str, rep: _Report, *, from_file: bool) -> Optional[str]:
    """The in-game file name of a drop, or None (anything unusable is dropped with a warning).
    Only a pack file (``from_file``: a v1.1 companion path) may be shortened to its file name."""
    raw = entry.get("file")
    if raw in (None, ""):
        if entry.get("inGameFile") is True:
            rep.warnings.append(f'{where}"inGameFile" without a "file" ignored')
        return None
    if not isinstance(raw, str):
        rep.warnings.append(f'{where}"file" dropped: not a file name')
        return None
    name = raw.strip()
    if is_bare_file_name(name):
        return name
    base = PureWindowsPath(name.replace("/", "\\")).name
    if from_file and base != name and is_bare_file_name(base):
        rep.warnings.append(f'{where}"file" {name!r} shortened to {base!r}: in-game files play from '
                            "Interface\\AddOns\\NeedleDropper_Local\\media")
        return base
    rep.warnings.append(f'{where}"file" {name!r} dropped: an in-game file needs a plain .mp3 or .ogg name '
                        "(letters, digits, spaces and ._-() only)")
    return None


def _comment_key(k: Any) -> bool:
    return isinstance(k, str) and k.startswith(("_", "$", "//"))


def _drop(entry: Mapping[str, Any], idx: int, rep: _Report, *,
          from_file: bool = False) -> Optional[tuple[str, dict[str, Any]]]:
    """``(place id, writer-form drop)`` for one entry, or None (problems go to ``rep``)."""
    where = f"drops[{idx}]: "
    place: dict[str, Any] = _place_numbers(entry, where, rep)
    place.update(_texts(entry, {"subzone": DROP_TEXT_CAPS["subzone"]}, where, rep))
    if entry.get("disabled") is True:
        did = _packs.disabled_drop_id(entry)
        if did is None:
            rep.warnings.append(f"{where}a turned-off entry without a valid place was dropped")
            return None
        return did, {"disabled": True, **place}
    parsed, _issues = _packs.parse_drop(dict(entry), idx)
    if parsed is None:
        return None  # parse_pack reports the same problems for the whole pack
    out: dict[str, Any] = dict(place)
    if "zone" not in out and parsed.mapID is not None and entry.get("zone") is None:
        out["zone"] = parsed.mapID  # the zone of a known areaID (packs.parse_pack fills it in)
    out.update(_texts(entry, {k: v for k, v in DROP_TEXT_CAPS.items() if k != "subzone"}, where, rep))
    if parsed.spotify:
        m = _SPOTIFY_URI_RE.fullmatch(parsed.spotify)
        if m:
            out["spotify"] = f"spotify:track:{m.group(1)}"
    if parsed.youtube:
        out["youtube"] = parsed.youtube
    for key in ("startMs", "durationMs"):
        v = int(getattr(parsed, key) or 0)
        if v > MAX_INT:
            rep.errors.append(f'{where}"{key}" must be at most {MAX_INT}, got {v}')
        elif v:
            out[key] = v
    if parsed.volume is not None:
        out["volume"] = int(parsed.volume)
    tags = _tags(entry.get("tags"), where, rep)
    if tags:
        out["tags"] = tags
    name = _file(entry, where, rep, from_file=from_file)
    if name:
        out["file"] = name
        out["inGameFile"] = True
        if entry.get("inGameFile") is not True:
            rep.warnings.append(f'{where}"file" {name!r} now plays in game: copy it into '
                                "Interface\\AddOns\\NeedleDropper_Local\\media")
    unknown = sorted(str(k) for k in entry if k not in _packs.KNOWN_DROP_KEYS and not _comment_key(k))
    if unknown:
        rep.warnings.append(f"{where}unknown key(s) {', '.join(map(repr, unknown))} dropped")
    if entry.get("appleMusic") not in (None, ""):
        rep.warnings.append(f'{where}"appleMusic" dropped (not used any more)')
    return parsed.id, out


def validate(pack: Any, *, name_hint: Optional[str] = None) -> tuple[dict[str, Any], list[str]]:
    """Validate a decoded pack object like a pack file (:func:`needledropper.packs.parse_pack`)
    plus the share-code rules of DESIGN 5.5; return ``(writer-form pack, warnings)``.

    Raises :class:`ShareCodeError` (``invalid``; ``issues`` = every error found). ``name_hint``
    names a pack file that has no ``"name"`` (a share code must carry its own).
    """
    return _validate(pack, name_hint=name_hint, from_file=False)


def _validate(pack: Any, *, name_hint: Optional[str], from_file: bool) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(pack, Mapping):
        raise ShareCodeError("invalid", issues=["the soundtrack is not a JSON object"])
    data = dict(pack)
    if name_hint and not (isinstance(data.get("name"), str) and data["name"].strip()):
        data["name"] = name_hint
    rep = _Report()
    parsed = _packs.parse_pack(copy.deepcopy(data), name_hint=name_hint or "soundtrack")
    for issue in parsed.issues:
        if issue.level == "error":
            rep.errors.append(str(issue))
        elif "unknown key(s)" not in issue.message:  # reported below: dropped, not kept
            rep.warnings.append(str(issue))
    unknown_top = sorted(str(k) for k in data if k not in _packs.KNOWN_PACK_KEYS and not _comment_key(k))
    if unknown_top:
        rep.warnings.append(f"unknown top-level key(s) {', '.join(map(repr, unknown_top))} dropped")
    texts = _texts(data, TOP_TEXT_CAPS, "", rep)
    if not texts.get("name"):
        rep.errors.append('the soundtrack needs a "name" (1-64 bytes)')
    tags = _tags(data.get("tags"), "", rep)

    drops: list[dict[str, Any]] = []
    raw_drops = data.get("drops")
    if isinstance(raw_drops, list):
        if not 1 <= len(raw_drops) <= MAX_DROPS:
            rep.errors.append(f"a soundtrack has 1 to {MAX_DROPS} drops, this one has {len(raw_drops)}")
        seen: dict[str, int] = {}
        for idx, entry in enumerate(raw_drops):
            if not isinstance(entry, dict) or entry.get("disabled") not in (None, True, False):
                continue  # parse_pack reported it
            got = _drop(entry, idx, rep, from_file=from_file)
            if got is None:
                continue
            did, out = got
            if did in seen:
                rep.errors.append(f"drops[{idx}]: the place {did!r} is listed twice (also drops[{seen[did]}])")
                continue
            seen[did] = idx
            drops.append(out)
    if not rep.errors and not parsed.id:
        rep.errors.append(f'cannot derive an "id" from the name {texts.get("name", "")!r}; add "id"')
    if rep.errors:
        raise ShareCodeError("invalid", issues=rep.errors)
    result: dict[str, Any] = {
        "format": _packs.PACK_FORMAT, "version": _packs.PACK_VERSION, "id": parsed.id, "name": texts["name"],
        "author": texts.get("author", ""), "description": texts.get("description", ""), "tags": tags,
        "drops": drops,
    }
    if texts.get("homepage"):
        result["homepage"] = texts["homepage"]
    if parsed.revision is not None:
        result["revision"] = parsed.revision
    return result, rep.warnings


# ------------------------------------------------------------- pack files --

def pack_for_sharing(data: Any, *, name_hint: str = "soundtrack") -> tuple[dict[str, Any], list[str]]:
    """A pack file's JSON object in share-code writer form, plus warnings.

    Like the addon's export: unfinished drops (no artist or no title, not turned off) are left
    out ("2 unfinished drops were not included"), zone names become uiMapIDs, Spotify links become
    ``spotify:track:<id>``, unknown keys and companion-only fields are dropped, and the result is
    checked with :func:`validate`. Raises :class:`ShareCodeError` (``invalid``) for a pack that
    cannot be shared.
    """
    if not isinstance(data, Mapping):
        raise ShareCodeError("invalid", issues=["the pack is not a JSON object"])
    data = dict(data)
    warnings: list[str] = []
    drops = data.get("drops")
    if isinstance(drops, list):
        kept: list[Any] = []
        drafts = 0
        for entry in drops:
            if isinstance(entry, dict) and entry.get("disabled") is not True and not (
                    _nonempty(entry.get("artist")) and _nonempty(entry.get("title"))):
                drafts += 1
                continue
            kept.append(_numeric_zone(entry))
        if drafts:
            warnings.append(f"{drafts} unfinished drop{'s were' if drafts != 1 else ' was'} not included "
                            "(a drop needs an artist and a title)")
        data["drops"] = kept
    out, more = _validate(data, name_hint=name_hint, from_file=True)
    return out, warnings + more


def _nonempty(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _numeric_zone(entry: Any) -> Any:
    """A drop entry with its ``zone`` (a name, alias, ``zone:<id>`` or digit string) as a uiMapID."""
    if not isinstance(entry, dict):
        return entry
    zv = entry.get("zone")
    if isinstance(zv, bool) or not isinstance(zv, (str, float)):
        return entry
    if isinstance(zv, float):
        return {**entry, "zone": int(zv)} if zv.is_integer() else entry
    z = zones.lookup(zv)
    if z is not None:
        return {**entry, "zone": z.id}
    s = zv.strip()
    return {**entry, "zone": int(s)} if s.isdigit() else entry
