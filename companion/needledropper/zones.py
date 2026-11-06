"""Canonical WoW: Forever zone table (uiMapIDs, Classic-style) and name lookup.

Pack authors may write zones by name ("Elwynn Forest", "the barrens",
"Un'Goro", "STV") or by uiMapID; :func:`lookup` resolves either. In game,
``/needle where`` prints the ids for the current location.

IDs marked "verified" in the docs were checked against wago.tools UiMap for
build 1.60.1.70790; the others follow the long-standing Classic numbering.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Union

KALIMDOR = "Kalimdor"
EASTERN_KINGDOMS = "Eastern Kingdoms"
ZEPHRAS = "Zephras Isle"
BATTLEGROUNDS = "Battlegrounds"

#: Continent uiMapIDs.
CONTINENTS: dict[int, str] = {1414: KALIMDOR, 1415: EASTERN_KINGDOMS}

ZONE_KINDS = ("zone", "city", "battleground", "start")


@dataclass(frozen=True)
class Zone:
    id: int  # uiMapID
    name: str
    continent: str
    kind: str  # "zone" | "city" | "battleground" | "start"


@dataclass(frozen=True)
class Area:
    """A known landmark area (areaID as returned by the client)."""

    id: int  # areaID
    name: str
    zone: int  # parent uiMapID


def _z(id_: int, name: str, continent: str, kind: str = "zone") -> Zone:
    return Zone(id_, name, continent, kind)


ZONES: list[Zone] = [
    # ---- Kalimdor (1414)
    _z(1411, "Durotar", KALIMDOR),
    _z(1412, "Mulgore", KALIMDOR),
    _z(1413, "The Barrens", KALIMDOR),
    _z(1438, "Teldrassil", KALIMDOR),
    _z(1439, "Darkshore", KALIMDOR),
    _z(1440, "Ashenvale", KALIMDOR),
    _z(1441, "Thousand Needles", KALIMDOR),
    _z(1442, "Stonetalon Mountains", KALIMDOR),
    _z(1443, "Desolace", KALIMDOR),
    _z(1444, "Feralas", KALIMDOR),
    _z(1445, "Dustwallow Marsh", KALIMDOR),
    _z(1446, "Tanaris", KALIMDOR),
    _z(1447, "Azshara", KALIMDOR),
    _z(1448, "Felwood", KALIMDOR),
    _z(1449, "Un'Goro Crater", KALIMDOR),
    _z(1450, "Moonglade", KALIMDOR),
    _z(1451, "Silithus", KALIMDOR),
    _z(1452, "Winterspring", KALIMDOR),
    _z(1454, "Orgrimmar", KALIMDOR, "city"),
    _z(1456, "Thunder Bluff", KALIMDOR, "city"),
    _z(1457, "Darnassus", KALIMDOR, "city"),
    _z(2482, "Mount Hyjal", KALIMDOR),  # new in Forever
    _z(2652, "Shen'dralas", KALIMDOR),  # new in Forever
    # ---- Eastern Kingdoms (1415)
    _z(1416, "Alterac Mountains", EASTERN_KINGDOMS),
    _z(1417, "Arathi Highlands", EASTERN_KINGDOMS),
    _z(1418, "Badlands", EASTERN_KINGDOMS),
    _z(1419, "Blasted Lands", EASTERN_KINGDOMS),
    _z(1420, "Tirisfal Glades", EASTERN_KINGDOMS),
    _z(1421, "Silverpine Forest", EASTERN_KINGDOMS),
    _z(1422, "Western Plaguelands", EASTERN_KINGDOMS),
    _z(1423, "Eastern Plaguelands", EASTERN_KINGDOMS),
    _z(1424, "Hillsbrad Foothills", EASTERN_KINGDOMS),
    _z(1425, "The Hinterlands", EASTERN_KINGDOMS),
    _z(1426, "Dun Morogh", EASTERN_KINGDOMS),
    _z(1427, "Searing Gorge", EASTERN_KINGDOMS),
    _z(1428, "Burning Steppes", EASTERN_KINGDOMS),
    _z(1429, "Elwynn Forest", EASTERN_KINGDOMS),
    _z(1430, "Deadwind Pass", EASTERN_KINGDOMS),
    _z(1431, "Duskwood", EASTERN_KINGDOMS),
    _z(1432, "Loch Modan", EASTERN_KINGDOMS),
    _z(1433, "Redridge Mountains", EASTERN_KINGDOMS),
    _z(1434, "Stranglethorn Vale", EASTERN_KINGDOMS),
    _z(1435, "Swamp of Sorrows", EASTERN_KINGDOMS),
    _z(1436, "Westfall", EASTERN_KINGDOMS),
    _z(1437, "Wetlands", EASTERN_KINGDOMS),
    _z(1453, "Stormwind City", EASTERN_KINGDOMS, "city"),
    _z(1455, "Ironforge", EASTERN_KINGDOMS, "city"),
    _z(1458, "Undercity", EASTERN_KINGDOMS, "city"),
    _z(2548, "Riverglades", EASTERN_KINGDOMS),  # new in Forever
    # ---- Zephras Isle (own continent; new Skyborne starting zone)
    _z(2521, "Zephras Isle", ZEPHRAS, "start"),
    # ---- Battlegrounds
    _z(1459, "Alterac Valley", BATTLEGROUNDS, "battleground"),
    _z(1460, "Warsong Gulch", BATTLEGROUNDS, "battleground"),
    _z(1461, "Arathi Basin", BATTLEGROUNDS, "battleground"),
    _z(2524, "Darkspear Islands", BATTLEGROUNDS, "battleground"),
]

ZONES_BY_ID: dict[int, Zone] = {z.id: z for z in ZONES}

#: Landmarks with known areaIDs (usable as ``"areaID"`` in packs).
KNOWN_AREAS: list[Area] = [
    Area(279, "Dalaran", 1416),  # open-world subzone of Alterac Mountains
    Area(16727, "Powderfuse Port", 2548),
    Area(17045, "Skywatcher Plateau", 1412),  # new in Forever
]
AREAS_BY_ID: dict[int, Area] = {a.id: a for a in KNOWN_AREAS}

#: Notable subzones (by uiMapID), mostly new Forever content, for pack authors.
KNOWN_SUBZONES: dict[int, list[str]] = {
    1412: ["Skywatcher Plateau"],
    1416: ["Dalaran"],
    1420: ["Bandarion Keep", "Whispering Forest", "Shadowvale"],
    1423: ["New Avalon"],
    2482: ["Elderwild", "Summit of Eternity", "Shrine of Aviana", "Mourning's Rest", "Felblood Scar"],
    2521: ["Valanaar", "Valanaar Skydocks"],
    2548: ["Farholde Keep", "Sunnyglade", "Powderfuse Port", "Rog'mar", "Eastwind Shore", "Windstead"],
}

#: Extra names people actually type (normalized on load).
ALIASES: dict[str, int] = {
    "STV": 1434, "Stranglethorn": 1434, "Strangle": 1434,
    "Hinterlands": 1425,
    "Barrens": 1413,
    "Stormwind": 1453, "SW": 1453,
    "Undercity": 1458, "UC": 1458,
    "Thunder Bluff": 1456, "TB": 1456,
    "Shendralas": 2652, "Shen Dralas": 2652,
    "Orgrimmar": 1454, "Org": 1454, "Orgri": 1454,
    "Ironforge": 1455, "IF": 1455,
    "Darnassus": 1457, "Darn": 1457,
    "Hyjal": 2482,
    "Ungoro": 1449, "Un Goro": 1449,
    "Elwynn": 1429,
    "Dun Morogh": 1426, "Morogh": 1426,
    "Tirisfal": 1420,
    "Silverpine": 1421,
    "Hillsbrad": 1424,
    "Alterac": 1416,
    "Arathi": 1417,
    "Redridge": 1433,
    "Loch": 1432,
    "Stonetalon": 1442,
    "Dustwallow": 1445,
    "Searing": 1427,
    "Steppes": 1428,
    "Deadwind": 1430,
    "Swamp": 1435, "Sorrows": 1435,
    "WPL": 1422, "Western Plague": 1422,
    "EPL": 1423, "Eastern Plague": 1423,
    "Needles": 1441, "1k Needles": 1441, "Thousand Needle": 1441,
    "Zephras": 2521,
    "Darkspear": 2524,
    "AV": 1459, "WSG": 1460, "AB": 1461,
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_APOSTROPHES = "'’‘`´"


def normalize(name: str) -> str:
    """Comparison key: case/accents/punctuation/apostrophes/leading "The" ignored.

    ``"The Barrens"`` -> ``"barrens"``, ``"Un'Goro Crater"`` -> ``"ungorocrater"``.
    """
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    for ch in _APOSTROPHES:
        s = s.replace(ch, "")
    s = re.sub(r"^the\s+", "", s)
    return _NON_ALNUM.sub("", s)


def _build_index() -> dict[str, int]:
    idx: dict[str, int] = {}
    for z in ZONES:
        idx[normalize(z.name)] = z.id
    for alias, zid in ALIASES.items():
        idx.setdefault(normalize(alias), zid)
    return idx


_INDEX = _build_index()


def lookup(name_or_id: Union[str, int, None]) -> Optional[Zone]:
    """Resolve a zone name, alias or uiMapID (int or numeric string)."""
    if name_or_id is None or isinstance(name_or_id, bool):
        return None
    if isinstance(name_or_id, int):
        return ZONES_BY_ID.get(name_or_id)
    s = str(name_or_id).strip()
    if not s:
        return None
    if s.isdigit():
        return ZONES_BY_ID.get(int(s))
    m = re.fullmatch(r"zone:(\d+)", s, re.IGNORECASE)
    if m:
        return ZONES_BY_ID.get(int(m.group(1)))
    zid = _INDEX.get(normalize(s))
    return ZONES_BY_ID.get(zid) if zid is not None else None


def suggest(name: str, n: int = 3) -> list[str]:
    """Closest canonical zone names, for "did you mean" messages."""
    key = normalize(name)
    names = {normalize(z.name): z.name for z in ZONES}
    for alias, zid in ALIASES.items():
        names.setdefault(normalize(alias), ZONES_BY_ID[zid].name)
    hits = difflib.get_close_matches(key, list(names), n=n * 2, cutoff=0.6)
    out: list[str] = []
    for h in hits:
        if names[h] not in out:
            out.append(names[h])
    return out[:n]


def lookup_area(name_or_id: Union[str, int, None], zone_id: Optional[int] = None) -> Optional[Area]:
    """Resolve a known landmark area by areaID or name (optionally within a zone)."""
    if name_or_id is None or isinstance(name_or_id, bool):
        return None
    if isinstance(name_or_id, int) or str(name_or_id).strip().isdigit():
        return AREAS_BY_ID.get(int(name_or_id))
    key = normalize(str(name_or_id))
    for a in KNOWN_AREAS:
        if normalize(a.name) == key and (zone_id is None or a.zone == zone_id):
            return a
    return None


def zones_by_continent() -> dict[str, list[Zone]]:
    """Zones grouped by continent, in table order of first appearance, sorted by name."""
    out: dict[str, list[Zone]] = {}
    for z in ZONES:
        out.setdefault(z.continent, []).append(z)
    for lst in out.values():
        lst.sort(key=lambda z: normalize(z.name))
    return out
