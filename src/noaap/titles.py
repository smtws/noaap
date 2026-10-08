"""Derive artist + song title from a YouTube video title and its channel (DESIGN.md §5).

Pure string work, no lookups. What cannot be decided from the text alone
(e.g. "Song - Artist" in reverse order on a lyrics channel) is left for the
MusicBrainz step or the user.
"""

from __future__ import annotations

import re

from .text import BRACKETS, SEPARATOR, clean_text, key, words_of

# bracket groups made only of these words are video noise, not part of the song title
NOISE_WORDS = {
    "official", "music", "video", "videoclip", "clip", "lyric", "lyrics", "with",
    "visualizer", "visualiser", "audio", "hd", "hq", "4k", "8k", "upgrade", "upgraded",
    "new", "premiere", "explicit", "mv", "360", "grad", "degree", "full", "fps", "60fps",
    "offizielles", "offizielle", "offizieller", "offiziell", "musikvideo", "musikclip",
    "oficial", "officiel", "ufficiale",  # the same label in other languages
    # resolutions: a video fact, never an audio one - unlike "(Remaster)", which stays
    "1080p", "720p", "480p", "2160p", "1440p", "360p", "240p", "144p", "uhd", "fullhd",
}
# ...but only when the group clearly labels the video (so "(Music of the Night)" stays intact)
MARKER_WORDS = {
    "official", "offizielles", "offizielle", "offizieller", "offiziell", "video", "videoclip",
    "clip", "visualizer", "visualiser", "lyric", "lyrics", "audio", "mv", "hd", "hq", "4k", "8k",
    "musikvideo", "musikclip", "oficial", "officiel", "ufficiale",
    "1080p", "720p", "480p", "2160p", "1440p", "360p", "240p", "144p", "uhd", "fullhd",
}

# a trailing segment naming a publisher: "… / Napalm Records". Unlike "|", a slash appears in
# real titles ("Intro / Outro", "AC/DC"), so the words have to say it is a label.
PUBLISHER_WORDS = {"records", "record", "recordings", "entertainment", "productions", "publishing", "media", "label", "musikverlag"}
_SLASH = re.compile(r"\s+/\s+")
# A dash separates when spaced on both sides, or - "Arcana- Innocent Child" - when what
# follows it starts a name: a German compound ellipsis continues in lowercase ("sang- und
# klanglos") and must stay whole. A colon separates too ("Metallica: Nothing Else Matters").
_QUOTED = re.compile(r'^(?P<artist>[^"“”„\']+?)\s*["“„\'](?P<title>[^"“”\']+)["”“\'](?P<rest>.*)$')
_LEADING_QUOTED = re.compile(r'^["“„](?P<title>[^"“”]+)["”“](?P<rest>.*)$')
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")  # bidi/zero-width marks
_BY = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<artist>[^()\[\]]+)$", re.I)


def before_label(text: str) -> str:
    """Drop a trailing "| Label", but only when the pipe stands outside every bracket.

    "Der Derwisch (Saltatio Mortis | Reading, Yoga & RPG Music)" keeps its bracket; cutting
    there used to leave it hanging open.
    """
    depth = 0
    for i, c in enumerate(text):
        if c in "([【":
            depth += 1
        elif c in ")]】":
            depth = max(0, depth - 1)
        elif depth == 0 and c == "|" and text[i - 1 : i] == " " and text[i + 1 : i + 2] == " ":
            return text[:i].rstrip()
    return text


def drop_label(text: str) -> str:
    """'Viva Vendetta | Napalm Records' -> 'Viva Vendetta': the uploader's label is not a name."""
    return _drop_publisher(before_label(clean_text(text))).strip(" -–—~")


def title_by_artist(title: str) -> tuple[str, str] | None:
    """'No Sound But The Wind by The Editors' -> ('The Editors', 'No Sound But The Wind').

    Only for titles that name no artist otherwise - plenty of songs have 'by' in them.
    """
    m = _BY.match(title.strip())
    return (m["artist"].strip(), m["title"].strip()) if m else None


_GROUP_ONLY = re.compile(r"\s*[(\[][^()\[\]]*[)\]]")
# words that say "another cut of the same song" rather than "another song". Wider than
# `enrich.VERSION_MARKERS`, which answers a different question (is this release the instrumental
# one); here a plain "mix" or "edit" has to count too, because that is what the damage looked like.
_VERSION_OF_A_SONG = re.compile(
    r"\b(version|versions|mix|mixes|remix|remixes|edit|edits|dub|instrumental|instrumentals|"
    r"acoustic|unplugged|live|demo|demos|karaoke|orchestral|symphonic|reprise|rehearsal|"
    r"single|album|film|radio|club|extended|vocal|original|mono|stereo|bonus)\b", re.I)


def strip_album_name(album: str, title: str) -> str:
    """Take the release name out of a track title, wherever the shop put it.

    "1 - Der Kuss des Kometen (Teil 01)" -> "Teil 01"
    "Kapitel 01: Die Hexenmeister des Metal (Folge 4)" -> "Kapitel 01 (Folge 4)" - the part number
    is kept, because it is what tells that recording from its siblings

    Only a run of at least two words counts, so a single-word album keeps its title track,
    and a title that would end up empty is left alone. Whether this is applied at all is an
    album-wide decision (`drop_album_name`) - alone it would turn "Carolus Rex (Swedish
    version)" into "Swedish version".
    """
    # NB not casefolded: casefold() maps "ß" to "ss", and the pattern is matched (case
    # insensitively) against the original title, where the "ß" is still there
    words = re.findall(r"\w+", album or "")
    if len(words) < 2 or not title:
        return title
    vocabulary = words_of(album)
    rest = title
    for n in range(len(words), 1, -1):
        run = r"\b" + r"\W+".join(map(re.escape, words[-n:])) + r"\b"  # \b: a bare "4" must not match inside "04"
        if re.search(run, rest, flags=re.I):
            rest = re.sub(run, "", rest, flags=re.I)
            break
    else:
        return title

    # "(Folge 4)" after the name is the release again, in brackets
    # a group that named the release, and the empty pair left when it sat inside one
    rest = BRACKETS.sub(lambda m: "" if not words_of(m[1]) or words_of(m[1]) <= vocabulary else m[0], rest)
    # a leading "2 - ". **Not across an opening bracket**: on "1 - Der Kuss des Kometen (Teil 01)"
    # a greedy \W+ ate the "(" too and left "Teil 01)" behind (R-521).
    rest = re.sub(r"^[^\w(\[]*\d+[^\w(\[]+", " ", rest) if words_of(rest) - vocabulary else rest
    # the name came out of the middle: "Vangelis - The City - Procession" must not become
    # "Vangelis - - Procession" (found 2026-10-08 in the full dry run, R-521)
    rest = re.sub(r"\s*([-–—:|])\s*(?:[-–—:|]\s*)+", r" \1 ", rest)
    rest = re.sub(r"\(\s*\)|\[\s*\]", " ", rest)
    rest = re.sub(r"\s*[:\-–—|]\s*(?=[(\[])", " ", rest)   # "Kapitel 01: (Folge 4)"
    rest = re.sub(r"\s+", " ", rest).strip(" -–—:|,.")
    inner = BRACKETS.fullmatch(rest)
    rest = (inner[1] if inner else rest).strip()
    # a title that *is* the album name leaves punctuation behind, not a name: "Drachentanz
    # (Live 2008)" on the album of that name strips to ")". Empty was already guarded; a remainder
    # with no word in it is the same thing wearing a bracket (found 2026-09-28, P51).
    if not re.search(r"\w", rest):
        return title
    # **what is left has to be able to be a song name** (R-521, found in the full dry run of
    # 2026-10-08). On a single the album name *is* the song name, so every track reads
    # "<album> (<version>)" and all of them are hits - the album-wide share guard cannot tell
    # that apart from a shop's prefix. Then the only thing stripping leaves is the bracket:
    # "Policy of Truth (Single Version)" became "single version", "Summer Wine (single edit)"
    # became "single edit", "Ai Vis Lo Lop (vocal remix)" became "vocal remix".
    #
    # What tells that apart from the audio play this rule was written for is **what the bracket
    # says**: "(Teil 01)" and "(Folge 4)" enumerate different recordings, "(single version)" and
    # "(Capitol mix)" name another cut of the one song. So a remainder that came out of the
    # brackets alone is refused only when it names a version; and a title never *begins* with
    # its own version marker.
    outside = words_of(_GROUP_ONLY.sub(" ", title))
    from_brackets_only = not words_of(rest) & outside
    if from_brackets_only and _VERSION_OF_A_SONG.search(rest):
        return title
    if rest.lstrip().startswith(("(", "[")):
        return title
    return rest


_CHANNEL_NOISE = re.compile(r"(\s*-\s*topic|vevo|\s*official)$", re.I)


_FEAT_WORD = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s", re.I)
_FEAT_TAIL = re.compile(r"\s*[(\[]?\s*\b(feat\.?|ft\.?|featuring)\s+(?P<guests>[^)\]]+?)\s*[)\]]?\s*$", re.I)


def channel_artist(channel: str | None) -> str | None:
    """'Mantus - Topic' -> 'Mantus', 'LACRIMOSAofficial' -> 'LACRIMOSA', 'SabatonVEVO' -> 'Sabaton'."""
    if not channel:
        return None
    name = clean_text(channel).strip()
    while (stripped := _CHANNEL_NOISE.sub("", name).strip()) != name:
        name = stripped
    return name or None


def clean_title(title: str) -> str:
    """Drop label suffixes, noise brackets like '(Official Video)', stray quotes and spacing."""
    title = before_label(clean_text(title))
    title = _drop_publisher(title)
    title = BRACKETS.sub(_clean_group, title)
    parts = SEPARATOR.split(title)
    while len(parts) > 1 and _is_noise(parts[-1]):  # "Song – Official Lyric Video"
        parts.pop()
    title = " - ".join(parts) if len(parts) > 1 else parts[0]
    title = _drop_noise_tail(title)  # "Gloria Offizielles Musikvideo", with no bracket or dash
    title = title.replace("@", "")  # "(feat. @handle)" -> "(feat. handle)"
    title = re.sub(r"\s+", " ", title).strip(" -–—~")
    if len(title) > 1 and title[0] in "\"“„'" and title[-1] in "\"”“'":
        title = title[1:-1].strip()
    return title


def parse_video_title(title: str, channel: str | None) -> tuple[str | None, str]:
    """Return (artist or None, song title). None means the title names no artist."""
    ch = channel_artist(channel)
    text = before_label(clean_text(title))

    parts = SEPARATOR.split(text, maxsplit=1)
    # '"Mad World" (feat. Gary Jules) - Official Music Video': what follows the dash only
    # labels the video, so the whole text is the song - it names no artist.
    if len(parts) == 2 and not _is_noise(parts[1]):
        left, right = clean_title(parts[0]), clean_title(parts[1])
        if ch and key(right) == key(ch) and key(left) != key(ch):
            left, right = right, left  # "Song - Artist" where the channel tells us the artist
        return _prefer_channel_spelling(left, ch), right

    if m := _QUOTED.match(text):
        return _prefer_channel_spelling(clean_title(m["artist"]), ch), clean_title(m["title"])

    cleaned = clean_title(text)
    if m := _LEADING_QUOTED.match(cleaned):  # nothing before the quoted song: no artist in the title
        return None, clean_title(f"{m['title']}{m['rest']}")

    return None, cleaned


def _prefer_channel_spelling(artist: str, ch: str | None) -> str:
    # channels are usually spelled properly ("Sabaton" vs a shouted "SABATON" title),
    # unless the channel name is all lowercase ("wardruna")
    return ch if ch and key(ch) == key(artist) and not ch.islower() else artist


def _clean_group(m: re.Match[str]) -> str:
    """'(Official Video)' -> '', '(Official Live Video)' -> ' (Live)', '(feat. X)' unchanged."""
    words = m[1].split()
    norm = [re.sub(r"\W", "", w).casefold() for w in words]
    if not MARKER_WORDS.intersection(norm):
        return m[0]
    kept = [w for w, n in zip(words, norm) if n not in NOISE_WORDS]
    if not kept:
        return ""
    opening, closing = m[0].strip()[0], m[0].strip()[-1]
    return f" {opening}{' '.join(kept)}{closing}"


def _drop_publisher(title: str) -> str:
    """'U-Gra (Tagelharpa playthrough) / Napalm Records' -> without the label."""
    parts = _SLASH.split(title)
    while len(parts) > 1 and PUBLISHER_WORDS.intersection(re.findall(r"\w+", parts[-1].casefold())):
        parts.pop()
    return " / ".join(parts)


def _drop_noise_tail(title: str) -> str:
    """Trailing video-label words that carry no punctuation: 'Strange World HD 1080p'.

    The whole trailing run of noise words is judged together and dropped only if one of them
    labels the video: "Full HD" goes, while "Life Is Full" keeps its last word, because
    "full" alone labels nothing.
    """
    words = title.split()
    plain = [re.sub(r"\W", "", w).casefold() for w in words]
    cut = len(words)
    while cut > 1 and plain[cut - 1] in NOISE_WORDS:
        cut -= 1
    if cut == len(words) or not any(w in MARKER_WORDS for w in plain[cut:]):
        return title
    return " ".join(words[:cut])


def _is_noise(group: str) -> bool:
    words = re.findall(r"\w+", group.casefold())
    return bool(MARKER_WORDS.intersection(words)) and all(w in NOISE_WORDS for w in words)
