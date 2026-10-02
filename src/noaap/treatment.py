"""The one state of the library, and the exceptions to it (DESIGN §9, slice 100).

The user asked the question that settles the design, twice. First: *"we had consistent state and now
we got persisted state per album that has to be manually overridden? what if i hit update or repair
after adoption, they follow system settings?"* And then, when a per-album record was proposed anyway:
*"if i do an intake with deliberately few options checked to speed things up and decide that i now
want to have the image embedded everywhere i am not going to hop through 700 albums manually.... "*

So there is no per-album policy:

1. **The settings are the state the library is in.** Every switch that says what an album should have
   — a cover beside it, a cover in the files, the words in the files, looked up at MusicBrainz and
   LRCLIB, and whether an adopted album is renamed into noaap's scheme or retagged at all — lives
   there, and nowhere else.
2. **A take-in's switches mean "do this now"**, so that eleven thousand tracks can be taken in
   quickly, and they are not recorded as anything. What the plan records is what was *done*, as it
   already did: adopted, names kept, tags kept.
3. **Every later pass brings the albums it touches to the settings.** What the settings ask for and
   an album lacks is done; what they do not ask for is left alone. Switching cover embedding on today
   means the next `repair --dry-run` lists every file without one and the apply embeds them — no
   visiting seven hundred albums.
4. **The only thing kept per album is an exception**, set by the user in the album view and by
   nothing else: *keep this album's names*, *do not embed here*. Update and repair honour them and
   say so, album by album, in the dry run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

# every switch that describes the state an album should be in, in the order a person reads them
OPERATIONS = ("musicbrainz", "lyrics", "cover_beside", "cover_embedded", "lyrics_embedded",
              "rename_adopted", "retag_adopted", "tidy_adopted_tags")

SAYS = {
    "musicbrainz": "looked up at MusicBrainz",
    "lyrics": "looked up at LRCLIB",
    "cover_beside": "a cover beside the album",
    "cover_embedded": "the cover in the files",
    "lyrics_embedded": "the words in the files",
    "rename_adopted": "adopted albums renamed into noaap's scheme",
    "retag_adopted": "adopted albums' tags rewritten",
    "tidy_adopted_tags": "adopted albums' values tidied as a fetched album's are",
}

# what a person may except one album from, and what each exception means
EXCEPTIONS = {
    "names": "keep this album's names",
    "tags": "do not write tags into this album's files",
    "cover_embedded": "do not embed the cover here",
    "lyrics_embedded": "do not embed the words here",
    "musicbrainz": "do not look this album up at MusicBrainz",
    "lyrics": "do not look this album's words up at LRCLIB",
}


@dataclass(frozen=True)
class Treatment:
    """What every album should have. One of these, from the settings, for the whole library."""

    musicbrainz: bool = True
    lyrics: bool = True
    cover_beside: bool = True
    cover_embedded: bool = True
    lyrics_embedded: bool = True
    rename_adopted: bool = False   # an adopted album keeps its owner's names unless this says otherwise
    retag_adopted: bool = False    # …and its owner's tags
    tidy_adopted_tags: bool = False  # …and its owner's values mean what they say (R-410, ruling 5)

    @staticmethod
    def from_settings(cfg: Any) -> Treatment:
        return Treatment(**{key: bool(getattr(cfg, key, Treatment.__dataclass_fields__[key].default))
                            for key in OPERATIONS})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def says(self) -> str:
        """One line: what the library is set to want."""
        on = [SAYS[key] for key in OPERATIONS if getattr(self, key)]
        return " · ".join(on) if on else "nothing beyond the files themselves"

    def off(self) -> list[str]:
        return [key for key in OPERATIONS if not getattr(self, key)]


# -- one album's exceptions ------------------------------------------------------------------------


def exceptions_of(plan: Any) -> dict[str, bool]:
    """What this album is excepted from, as the user set it. Never written by a pass."""
    said = getattr(plan, "exceptions", None) or {}
    return {key: bool(value) for key, value in said.items() if key in EXCEPTIONS and value}


def for_album(cfg: Any, plan: Any) -> Treatment:
    """The settings as they apply to **this** album: the library's state, minus its exceptions.

    An exception can only take something away. There is no way for an album to ask for more than the
    library is set to do, because then the library would not be in one state — which is the whole
    complaint this design answers.
    """
    want = Treatment.from_settings(cfg)
    excepted = exceptions_of(plan)
    if not excepted:
        return want
    off = dict(want.as_dict())
    for key in ("cover_embedded", "lyrics_embedded", "musicbrainz", "lyrics"):
        if excepted.get(key):
            off[key] = False
    if excepted.get("names"):
        off["rename_adopted"] = False
    if excepted.get("tags"):
        off["retag_adopted"] = False
    return Treatment(**off)


def held_back(cfg: Any, plan: Any) -> list[str]:
    """Which of the settings this album's own exceptions hold back, for a dry run to say."""
    want, mine = Treatment.from_settings(cfg), for_album(cfg, plan)
    return [key for key in OPERATIONS if getattr(want, key) and not getattr(mine, key)]


def says_exceptions(plan: Any) -> str:
    """The album's exceptions as a sentence, for the album view and the dry run."""
    mine = exceptions_of(plan)
    return " · ".join(EXCEPTIONS[key] for key in EXCEPTIONS if mine.get(key))


def with_exception(plan: Any, key: str, on: bool) -> dict[str, bool]:
    """The album's exceptions with one turned on or off — what the album view sends."""
    if key not in EXCEPTIONS:
        raise ValueError(f"no such exception: {key}")
    mine = exceptions_of(plan)
    if on:
        mine[key] = True
    else:
        mine.pop(key, None)
    return mine


# -- what it comes to for one album's files ----------------------------------------------------------
#
# An adopted album keeps its owner's names and tags — that is what adoption means, and it is recorded
# on the plan as a fact. Whether the library nonetheless wants such an album renamed into noaap's
# scheme, or its tags rewritten, is a **setting**; an album excepted from it keeps them either way.


def renames(plan: Any, want: Treatment | None) -> bool:
    """Whether this album's files may be renamed into noaap's scheme."""
    if not getattr(plan, "keep_names", False):
        return True
    return bool(want and want.rename_adopted)


def retags(plan: Any, want: Treatment | None) -> bool:
    """Whether noaap's tags may be written into this album's files."""
    if not getattr(plan, "keep_tags", False):
        return True
    return bool(want and want.retag_adopted)


def known_fields() -> tuple[str, ...]:
    return tuple(f.name for f in fields(Treatment))


__all__ = ["EXCEPTIONS", "OPERATIONS", "SAYS", "Treatment", "exceptions_of", "for_album",
           "held_back", "known_fields", "renames", "retags", "says_exceptions", "with_exception"]
