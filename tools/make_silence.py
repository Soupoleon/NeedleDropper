"""Generate addon/NeedleDropper/media/silence.mp3 — a few seconds of digital silence.

PlayMusic() on this file fades out WoW's built-in zone music without touching
any CVar (the "empty track" technique used by the Soundtrack addon).

Each frame is a valid MPEG-1 Layer III frame (32 kbps, 44.1 kHz, mono) whose
side info and main data are all zero, which every decoder renders as silence.
No encoder needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

SAMPLE_RATE = 44100
BITRATE = 32000
SAMPLES_PER_FRAME = 1152
# MPEG-1 Layer III, no CRC | bitrate idx 1 (32k), 44.1 kHz, no padding | mono
HEADER = bytes([0xFF, 0xFB, 0x10, 0xC0])
FRAME_LEN = 144 * BITRATE // SAMPLE_RATE  # 104 bytes


def silent_mp3(seconds: float = 5.0) -> bytes:
    frames = max(1, round(seconds * SAMPLE_RATE / SAMPLES_PER_FRAME))
    frame = HEADER + bytes(FRAME_LEN - len(HEADER))
    return frame * frames


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "addon" / "NeedleDropper" / "media" / "silence.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = silent_mp3()
    out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
