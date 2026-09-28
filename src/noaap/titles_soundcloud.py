"""What a SoundCloud title means (DESIGN §9, slice 57).

Its conventions are **not** YouTube's. There is no "Official Video", no Vevo, no "- Topic" channel.
What there is, from the real pages this was written against:

    Divine Alliance (epic heroic power metal)      a genre in the title, which is SEO, not a title
    Carmina Gloria (symphonic crusader power metal)
    Bloodywood - Gaddaar (Indian Folk Metal)       an `Artist - Title` prefix, as on YouTube
    Auf Wiederseh'n (snip)                          a preview, not the song

So three rules and no more. **A parenthesis is only dropped when it says something about the
upload** — a genre, a snippet, a format. `(Live)`, `(Acoustic)`, `(Remix)` and `(feat. …)` are part
of the song and are kept, which is the mistake that would otherwise merge two different recordings.
"""

from __future__ import annotations

import re

# a trailing parenthesis that describes the upload rather than the song
NOISE = re.compile(
    r"\s*[(\[]\s*(?:"
    r"snip|snippet|preview|teaser|clip|free\s*download|out\s*now|hq|hd|full|original\s*mix|"
    r"[\w'’\- ]*\b(?:metal|rock|pop|folk|punk|rap|techno|house|trance|ambient|soundtrack|"
    r"orchestral|symphonic|instrumental\s+music|edm|dnb|drum\s*&\s*bass)\b[\w'’\- ]*"
    r")\s*[)\]]\s*$",
    re.I,
)
# "Artist - Title", the one convention the two sites share
SPLIT = re.compile(r"^\s*(?P<artist>.{1,80}?)\s+[-–—]\s+(?P<title>.+?)\s*$")


def clean_title(title: str) -> str:
    """Strip what the uploader added for the search box, and nothing else."""
    out = (title or "").strip()
    for _ in range(3):  # "Song (epic folk metal) (snip)" — two of them happen
        stripped = NOISE.sub("", out).strip()
        if stripped == out:
            break
        out = stripped
    return out or (title or "").strip()


def parse_track_title(title: str, uploader: str | None = None) -> tuple[str | None, str]:
    """(artist, title) as SoundCloud's conventions read them.

    The artist is only ever taken from an `Artist - Title` split. **The uploader is not returned
    as the artist here**: `owner_artist` is where that belongs, and answering it twice would make
    an upload by a curator look as if the song itself said so.
    """
    cleaned = clean_title(title)
    if m := SPLIT.match(cleaned):
        artist, rest = m["artist"].strip(), m["title"].strip()
        if artist and rest:
            return artist, rest
    return None, cleaned


def owner_is_artist(owner: str | None) -> str | None:
    """An uploader usually is the artist — but a shop sign is not a name.

    SoundCloud has no "- Topic" convention, so there is nothing to strip; what there is instead is
    the occasional profile that announces itself as a service ("Ebunny. Music for your projects.").
    A name with a sentence in it is not an artist, and the classifier is better off with nothing.
    """
    name = (owner or "").strip()
    if not name or len(name) > 60 or name.count(".") > 1 or ", " in name:
        return None
    return name
