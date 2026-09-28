"""Where a file's audio stops (DESIGN §9, slice 54).

A container and a bitrate are claims; this is a measurement. It exists because the reference
collection is full of files whose claim is false — 52% of its 24-bit/48 kHz FLACs are band-limited
at 20-21 kHz, exactly where its Opus files sit, which is what a lossless container holding a lossy
source looks like from the outside.

**The method.** One decode of a window from the middle of the track, split into 1 kHz bands from
14 to 23 kHz, each band's RMS measured by ffmpeg's own `astats`. The highest band still above the
floor is where the audio stops. No DSP in Python and nothing to install: ffmpeg is already required
for trimming.

**Two readings that are not the same thing.** A band below `EMPTY` is not quiet — it is above that
file's Nyquist, and 44.1 kHz audio cannot hold anything over 22.05 kHz whatever its encoder did. So
a file is measured against *its own* ceiling, and `full` says it reached it. A full-band CD rip
reads 22 and a full-band 48 kHz file reads 24; the CD rip is not the worse file, which is why
`full` is compared before the number ever is.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tag import audio_quality

BANDS = (14, 15, 16, 17, 18, 19, 20, 21, 22, 23)  # kHz, each one 1 kHz wide
EMPTY = -150.0   # below this there is no signal at all: the band is above the file's Nyquist
FLOOR = -85.0    # a backstop: below this nobody hears anything whatever the rest of the file does
# …and the test that actually decides. Opus carries nothing above 20 kHz by design, yet its files
# leave 40–50 dB of decoder residue in the bands above; measured against an absolute floor alone,
# a quiet track's residue reads as content and the file looks full-band. So a band counts only if
# it is within this much of the 14–15 kHz band, which is reliably music in anything worth keeping.
BELOW = -30.0
WINDOW = (30.0, 20.0)  # from 30 s in, 20 s long: past the intro, inside the song

_RMS = re.compile(r"Parsed_astats_(\d+) @ [^\]]+\] RMS level dB: (-?\d+\.?\d*|-inf)")


@dataclass
class Spectrum:
    """What the bands said, and what that means about this file."""

    cutoff: int | None = None       # kHz: the top of the highest band holding content
    full: bool = False              # …and that band is the highest this sample rate allows
    why: str = ""
    bands: tuple[float, ...] = ()

    @property
    def known(self) -> bool:
        return self.cutoff is not None

    def to_dict(self) -> dict[str, object]:
        return {"cutoff_khz": self.cutoff, "full_band": self.full}


def profile(path: Path, window: tuple[float, float] = WINDOW) -> list[float] | None:
    """Each band's RMS in dB, in one pass over the window.

    `asplit` feeds one `astats` per band, so ten probes cost one decode rather than ten.
    """
    n = len(BANDS)
    chain = ["[0:a]asplit=" + str(n) + "".join(f"[a{i}]" for i in range(n))]
    for i, khz in enumerate(BANDS):
        lo, hi = khz * 1000, (khz + 1) * 1000
        chain.append(f"[a{i}]firequalizer=gain='if(between(f,{lo},{hi}),0,-200)',"
                     f"astats=measure_overall=RMS_level:measure_perchannel=none[o{i}]")
    maps: list[str] = []
    for i in range(n):
        maps += ["-map", f"[o{i}]", "-f", "null", "-"]
    try:
        done = subprocess.run(
            ["ffmpeg", "-v", "info", "-ss", str(window[0]), "-t", str(window[1]), "-i", str(path),
             "-filter_complex", ";".join(chain), *maps],
            capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    # the filters report in whatever order they finish; the astats index says which band it was
    found = {int(i): (-99.0 if v == "-inf" else float(v)) for i, v in _RMS.findall(done.stderr)}
    if len(found) < n:
        return None
    return [found[i] for i in sorted(found)]


def measure(path: Path, window: tuple[float, float] = WINDOW) -> Spectrum:
    """Where this file's audio stops, or why that cannot be said."""
    bands = profile(path, window)
    if bands is None:
        return Spectrum(why="not measurable")
    reference = bands[0]  # the 14–15 kHz band: music, in anything this measurement is asked about
    loud = [khz for khz, v in zip(BANDS, bands, strict=True)
            if v > EMPTY and v > FLOOR and v - reference > BELOW]
    if not loud:
        # a quiet recording, an old master: there is nothing up here to judge by, and that is an
        # answer rather than a bad score. Nothing is ever replaced on the strength of it.
        return Spectrum(why="nothing up here to judge by", bands=tuple(bands))
    # the file's own ceiling, from its sample rate: 22 kHz for a CD rip, 24 for anything at 48.
    # Opus reports none of its own and is always 48 kHz inside.
    rate = audio_quality(path).get("sample_rate") or 48000
    ceiling = int(rate) // 2000
    # the band that straddles Nyquist holds real audio in its lower sliver, so a flat source can
    # push the top band past the ceiling. A file cannot hold more than its rate allows.
    cutoff = min(max(loud) + 1, ceiling)
    return Spectrum(cutoff=cutoff, full=cutoff >= ceiling, bands=tuple(bands),
                    why="at this file's ceiling" if cutoff >= ceiling else "band-limited")


def better(a: Spectrum, b: Spectrum, margin: int = 1) -> int:
    """1 if `a` holds more audio than `b`, -1 if less, 0 if there is nothing to choose.

    **Reaching its own ceiling beats a wider number.** A CD rip stops at 22 kHz because that is
    where 44.1 kHz audio stops, not because anything was thrown away; a 48 kHz file band-limited
    at 21 kHz had something thrown away. Comparing the numbers first would get that backwards.
    """
    if not a.known or not b.known:
        return 0
    if a.full != b.full:
        return 1 if a.full else -1
    if a.full and b.full:
        return 0  # both kept everything their rate allows: this measurement has no more to say
    if abs(a.cutoff - b.cutoff) < margin:
        return 0
    return 1 if a.cutoff > b.cutoff else -1
