"""Pipeline stage 4b: MusicBrainz enrichment (DESIGN.md §4, slice 5).

MusicBrainz is an enricher, never a gatekeeper: nothing is dropped when it has no match.
- album level (official albums, artist playlists): accept a release only if title and
  artist match, the track count is within ±1 and ≥80% of the tracks match by title;
  then album/artist/year/tracklist/cover come from it.
- track level (everything not covered by a release): a recording must match title AND
  artist; if nothing matches, the swapped query catches "Song - Artist" titles.
Values set here become the plan's auto values, so user edits still win on merge.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
from collections import Counter
from collections.abc import Callable, Collection
from typing import Any

from .download import CAA, caa_release_front
from .mb import MusicBrainzAPI, MusicBrainzError
from .models import AlbumPlan, Kind, PlanTrack, Provenance
from .plan import FOLDER, LENGTH_SLACK, fits, owner_artist_of, refresh_derived, without_album_name
from .text import key, move_feat, title_key

log = logging.getLogger(__name__)

# worldwide/European editions first (canonical tracklists), then big markets
COUNTRY_ORDER = ["XW", "XE", "DE", "US", "GB", "SE", "CA", "AU", "FR", "NL", "NO", "FI", "JP"]
MIN_TRACK_MATCH = 0.8
RELEASE_LOOKUPS = 3  # candidates to open per album (each is one request)

_GROUP = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]")
# Bracket groups that say the recordings themselves are different ones. An edition marker
# ("Deluxe Edition", "Tour Edition", "Collector's Cut") names the same songs and MusicBrainz
# is right to drop it; these do not. Measured on 35 library albums: 5 carried an edition
# marker YouTube had and MusicBrainz lacked, 1 a version marker — "OPVS NOIR Vol. 1
# (Instrumental)", which had been filed as the ordinary album (DESIGN.md §9, slice 19).
VERSION_MARKERS = (
    "instrumental", "instrumentals", "karaoke", "acoustic", "unplugged", "live", "demo", "demos",
    "commentary", "remix", "remixes", "a cappella", "acapella", "orchestral", "symphonic",
    "radio edit", "sped up", "slowed", "reprise", "rehearsal", "backing track",
)
_MARKER = re.compile(r"\b(" + "|".join(VERSION_MARKERS) + r")\b", re.I)
_FEAT = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s", re.I)


# -- title helpers -------------------------------------------------------------------------


def core(title: str) -> str:
    """The comparable part of a title: no bracket groups, no trailing 'feat. X'."""
    title = _GROUP.sub("", title)
    title = _FEAT.split(title)[0]
    return title.strip(" -–—")


# **a disc or part marker in an album's own name** (§9, slice 146). 39 of the first 517 albums of
# the user's library are one disc of a set kept as its own folder: `Requiembryo (CD 1)`,
# `Horror Vacui (CD1)`, `The Better Life [Deluxe Edition] Disc 1`, `Zaubererbruder … 1`. No release
# is called that, so the search either returns nothing or returns the whole set, whose track count
# the filter then rejects. Bracketed or not, at the end of the name, and a number is required: an
# album really called `Disintegration` keeps its name, and `Teil` without a number is somebody's
# title (`Der schwarze Schmetterling, Teil V` is a release, not a disc marker).
_DISC_MARKER = re.compile(
    r"[\s,._-]*[(\[]?\s*(?:cd|disc|disk|dis[ck]o|part|pt|teil|vol(?:ume)?)\s*\.?\s*(\d{1,2})\s*[)\]]?\s*$",
    re.IGNORECASE)


def disc_in_name(album: str) -> tuple[str, int] | None:
    """`('Requiembryo', 1)` for `Requiembryo (CD 1)`, or None where the name says no such thing."""
    if not (m := _DISC_MARKER.search(album or "")):
        return None
    rest = album[:m.start()].strip(" -–—,._([")
    if not rest:
        return None             # the whole name was the marker: it names nothing to search for
    return rest, int(m.group(1))


def feat_text(title: str) -> str:
    """Everything that names featured artists, e.g. '(feat. @xxFEUERSCHWANZxx)' -> 'xxfeuerschwanzxx'."""
    found = [g for g in _GROUP.findall(title) if _FEAT.search(g + " ")]
    tail = _FEAT.split(_GROUP.sub("", title), maxsplit=1)
    if len(tail) == 2:
        found.append(tail[1])
    return key(" ".join(found))


def kept_suffixes(ours: str, theirs: str, albums: list[str] = ()) -> str:
    """Bracket groups of our title that MusicBrainz' title lacks (e.g. '(Live)').

    Not kept: feat. credits (they go into the artist) and groups naming a release the
    recording appeared on ('[MASKENHAFT-Ein Versinken in elf Bildern]') - album info.
    """
    album_keys = [key(a) for a in albums if a]
    kept = []
    for m in _GROUP.finditer(ours):
        g, k = m[0].strip(), key(m[1])
        if _FEAT.search(m[1] + " ") or not k or k in key(theirs):
            continue
        if any(k in a or a in k for a in album_keys):
            continue
        kept.append(g)
    return "".join(f" {g}" for g in kept)


def version_markers(title: str) -> set[str]:
    """What a title's bracket groups say this release *is*: {'instrumental'}, {'live'}, …"""
    return {m.lower() for group in _GROUP.findall(title) for m in _MARKER.findall(group)}


# what a bracket says beyond `VERSION_MARKERS`: German spellings, and the words a sleeve uses for
# another take of one song — `Saigon und Bagdad (Club Mix)`, `Schau in Mein Gesicht (Akkustik
# Version)`. Not `version` on its own: `(Album Version)` is the same take as the plain title.
# **and at release level too** (§9, slice 177; R-584): `Fly on the Windscreen (final)`, `Any Second Now
# (Voices)`, `Schwarzer Stern (reworked by Terminal Choice)` are takes a release names, and an update
# that crossed them replaced an edition that had them with one that did not.
_OTHER_TAKES = re.compile(r"\b(mix|edit|akustik|akkustik|orchester|live|final|voices|reworked)\b", re.I)


def takes(title: str) -> set[str]:
    """Which take a title names, from its brackets: empty for the plain song (§9, slices 162/163)."""
    return version_markers(title or "") | {m.lower() for g in _GROUP.findall(title or "")
                                           for m in _OTHER_TAKES.findall(g)}


def credited(entry: dict[str, Any]) -> str:
    """One artist of a credit, in their own spelling when the credit only restyles it.

    MusicBrainz credits carry per-release typography: three of seven Visions of Atlantis
    releases credit "Visions Of Atlantis", and a library then splits in two over the
    capital O. A credit that names something *else* ("Puff Daddy" for the artist "Diddy")
    is a deliberate editorial decision and is kept — hence the equivalence check.
    """
    credit = entry.get("name") or ""
    entity = (entry.get("artist") or {}).get("name") or ""
    if credit and entity and key(credit) == key(entity):
        return entity
    return credit or entity


def credit_phrase(ac: list[dict[str, Any]]) -> str:
    return "".join(credited(a) + a.get("joinphrase", "") for a in ac).strip()


def credit_names(ac: list[dict[str, Any]]) -> list[str]:
    return [credited(a) for a in ac]


def artist_matches(ours: str, ac: list[dict[str, Any]]) -> bool:
    """Our artist is the whole credit, or its main artist (as credited or canonical name)."""
    if not ac or not (k := key(ours)):
        return False
    main = ac[0]
    return k in {key(credit_phrase(ac)), key(main.get("name", "")), key(main.get("artist", {}).get("name", ""))}


# -- track level ---------------------------------------------------------------------------


def pick_recording(artist: str, title: str, recordings: list[dict[str, Any]],
                   seconds: float | None = None) -> dict[str, Any] | None:
    """The recording this track is, of a search's answers — or None.

    **Not one longer than the video** (§9, slice 204; q26, R-688). `Evanescence — Going Under`, a
    219-s video, took `954b2a1f`, the São Paulo 2023 live recording of 261 s: the only answer on an
    official album, none of the ten the studio one. With the video's `seconds`, a recording longer
    by more than `LENGTH_SLACK` is not this one and the next in rank is taken. Shorter stays
    allowed — a video with film around the song is longer than its recording, and that length is
    what the trim suggestion cuts to — and a recording of no known length passes, as before.
    """
    want, feat = title_key(core(title)), feat_text(title)
    ok = [r for r in recordings if title_key(core(r.get("title", ""))) == want and artist_matches(artist, r.get("artist-credit", []))
          and not (seconds and r.get("length") and r["length"] / 1000 > seconds + LENGTH_SLACK)]
    if not ok:
        return None

    def rank(r: dict[str, Any]) -> tuple:
        others = [key(n) for n in credit_names(r["artist-credit"])[1:]]
        feat_hit = bool(feat) and any(n and n in feat for n in others)
        unexpected_guests = bool(others) and not feat
        official_album = any(
            rel.get("status") == "Official" and (rel.get("release-group") or {}).get("primary-type") == "Album"
            for rel in r.get("releases", [])
        )
        return (not feat_hit, unexpected_guests, -int(r.get("score", 0)), not official_album)

    return min(ok, key=rank)


def uploader_stood_in(t: PlanTrack, source: Any = None) -> bool:
    """Nothing named the artist, so the collection's owner did — a name MusicBrainz will not know."""
    return bool(t.owner) and key(t.artist) == key(owner_artist_of(t.owner, source) or "")


def split_lookup(title: str, mb: MusicBrainzAPI) -> tuple[str, str, dict[str, Any]] | None:
    """'Assemblage 23 Lullaby' -> artist 'Assemblage 23', title 'Lullaby'.

    Where no punctuation separates the two, try every split and let MusicBrainz decide: a
    candidate counts only if it matches both halves (`pick_recording` checks artist and
    title), so a wrong band cannot come back from a query for the right words.
    """
    words = title.split()
    for i in range(1, len(words)):
        artist, rest = " ".join(words[:i]), " ".join(words[i:])
        if rec := pick_recording(artist, rest, mb.search_recordings(artist, core(rest) or rest)):
            return artist, rest, rec
    return None


# a hit that is another version of the song, unless the release's own title says so too (§9, slice
# 162); here since slice 206 asks it of a recording too (q29d), and `complete` imports it from here
OTHER_VERSIONS = ("live", "unplugged", "acoustic", "remix", "cover", "karaoke", "instrumental",
                  "nightcore", "sped up", "slowed", "8d", "reaction", "tutorial", "piano",
                  # round I (R-720, q31): `Watching Over Me (Alive in Athens 1999)`, `Elegy (Dvd)`,
                  # `Where You Are (Club Version)` were offered as the album take
                  "alive", "dvd", "club version")


def other_version(rec: dict[str, Any], *ours: str) -> bool:
    """Whether a recording is another version than ours: it says a marker — `live, 2006-10-10:
    Philadelphia`, `(live)` — that none of `ours` (the track's title, the album's) says (§9, slice 206;
    q29d, R-709), or one of ours says a marker it does not (q29e, R-710): `Wildlive`'s `1589` is not
    the `Wake Up the Wicked` studio take, whatever its length."""
    theirs = f"{rec.get('title') or ''} {rec.get('disambiguation') or ''}".casefold()
    mine = " ".join(ours).casefold()

    def says(text: str, w: str) -> bool:
        return bool(re.search(rf"\b{re.escape(w)}\b", text))

    return any(says(theirs, w) != says(mine, w) for w in OTHER_VERSIONS)


def ask_again(t: PlanTrack, mb: MusicBrainzAPI, album: str = "") -> bool:
    """**A re-ask after a take-back attaches only a recording that fits** (§9, slice 206; q29c, R-706).

    Round H's second dry, asked under slice 204's one-sided gate, offered `Lord of the Lost — Ruins`
    (a 316-s video) a 274-s recording and with it the title `Ruins (Alternative Version)`, and
    `Saltatio Mortis — Früher war alles besser` (317 s) one of 238 s. A track whose recording was
    just taken back is asked more strictly: a recording of known length within `LENGTH_SLACK` of
    the video, either way. One that does not fit confirms nothing — no id, and the title and
    artist stay as they are; one of no known length is no answer either.

    **And not another version of the song** (q29d, R-709): the length alone gave `Bring Me to Life`
    a 2006 Philadelphia bootleg and `Attero Dominatus` a 2016 live video. A recording whose title or
    disambiguation says `live`, `acoustic`, … is refused unless the track's or the album's title
    says it too — `Powerwolf — Wildlive`'s `Stossgebet` takes the Olympiahalle recording.
    """
    if not t.duration:
        return False
    rec = fitting_recording(t, mb.search_recordings(t.artist, core(t.title) or t.title), t.duration,
                            album, getattr(t, "refused_ids", ()))
    if rec is None:
        return False
    t.mbid, t.mb_length = rec["id"], round(rec["length"] / 1000, 1)
    return True


def fitting_recording(t: PlanTrack, answers: list[dict[str, Any]], seconds: float | None, album: str = "",
                      refused: Collection[str] = ()) -> dict[str, Any] | None:
    """The recording among `answers` that is this track's at `seconds`: of known length within
    `LENGTH_SLACK` either way, not another version, not one refused (§9, slices 206, 210)."""
    if not seconds:
        return None
    found = [r for r in answers
             if r.get("length") and fits(r["length"] / 1000, seconds)
             and not other_version(r, t.title, album)    # length alone took live bootlegs (q29d)
             and r.get("id") not in refused]              # nor one taken back (q29f)
    return pick_recording(t.artist, t.title, found)


def enrich_track(t: PlanTrack, mb: MusicBrainzAPI, source: Any = None, album: str = "") -> bool:
    if getattr(t, "ask_again", False):
        return ask_again(t, mb, album)
    asked = (t.artist, t.title, mb.search_recordings(t.artist, core(t.title) or t.title))
    rec = pick_recording(*asked)
    title_source = t.title
    if rec is None:  # maybe "Song - Artist": swap
        swapped_artist, swapped_title = core(t.title), t.artist
        if swapped_artist and swapped_title:
            asked = (swapped_artist, swapped_title, mb.search_recordings(swapped_artist, swapped_title))
            rec = pick_recording(*asked)
            title_source = swapped_title
    if rec is None and uploader_stood_in(t, source) and (found := split_lookup(t.title, mb)):
        _, title_source, rec = found
        asked = (found[0], found[1], [rec])
    if rec is None:
        return False
    # **not a recording longer than the video** (§9, slice 204; q26): the one attached is the next in
    # rank that is not, and where none is the lookup still confirms the artist and spelling, as a
    # variant does, and attaches nothing. **Nor another version of the song** (§9, slice 209; q31,
    # R-724): a first lookup took the 2006 Philadelphia bootleg of `Bring Me to Life` as readily as
    # the re-ask of slice 206 did before q29d — the same symmetric marker rule, on every pick.
    seconds = None if t.source_override else t.duration     # the video's; not one whose audio is another's

    def unfit(r: dict[str, Any]) -> bool:
        return not pick_recording(asked[0], asked[1], [r], seconds) or other_version(r, asked[1], album)

    too_long = unfit(rec)
    if too_long and not kept_suffixes(title_source, rec["title"], [r.get("title", "") for r in rec.get("releases", [])]):
        rec = pick_recording(asked[0], asked[1], [r for r in asked[2] if not unfit(r)], seconds) or rec
        too_long = unfit(rec)

    ac = rec["artist-credit"]
    extra = kept_suffixes(title_source, rec["title"], [r.get("title", "") for r in rec.get("releases", [])])
    title = rec["title"] + extra
    if feat_text(title_source) and len(ac) == 1:  # MB has no guest credit: keep ours
        title += "".join(f" {g}" for g in re.findall(r"\([^)]*feat[^)]*\)", title_source, re.I))
    artist, title = move_feat(credit_phrase(ac), title)  # "A feat. B" - "Song" -> "A" - "Song feat. B"
    _set(t, "artist", artist)
    if extra or too_long:
        # "(Live)", "(Behind The Scenes Documentary)": the artist is confirmed, but this is
        # not that recording - keep our title, attach no recording id, and take no length
        # from it either (a live cut measured against the studio one reads as 2 minutes off)
        if t.provenance.get("title") in FIRSTHAND:
            # **our title, letter for letter** (§9, slice 193; q17): `title` is MusicBrainz' spelling
            # with our bracket put back, so `We’re Not Gonna Take It (Single Version)` in the file
            # came back as `We're …` and a pass that met the other recording first wrote `’` again
            t.auto["title"] = title
            return True
        t.title = t.auto["title"] = title
        # **and keeping a title must not cost it its standing** (§9, slice 153; R-525). This wrote
        # `SOURCE_TITLE` over whatever was there, so a `file_tags` title that survived one lookup
        # stopped being firsthand and the *next* one could replace it.
        if t.provenance.get("title") not in FIRSTHAND:
            t.provenance["title"] = Provenance.SOURCE_TITLE
    else:
        _set(t, "title", title)
        t.mbid = rec["id"]
        if length := rec.get("length"):
            t.mb_length = round(length / 1000, 1)  # lets the UI suggest where the song ends
    return True


# -- album level ---------------------------------------------------------------------------


def _country_rank(country: str | None) -> int:
    return COUNTRY_ORDER.index(country) if country in COUNTRY_ORDER else len(COUNTRY_ORDER)


def release_candidates(plan: AlbumPlan, releases: list[dict[str, Any]], title: str | None = None,
                       any_count: bool = False) -> list[dict[str, Any]]:
    """The releases worth opening for this album.

    `title` asks under another name than the album's own — what slice 146 needs when the folder is
    called `Requiembryo (CD 1)` and no release is. `any_count` drops the track-count test with it,
    because one disc of a set never has the set's count and only opening the release can say which
    medium fits.
    """
    n = len(plan.tracks)
    want = title if title is not None else plan.album
    ok = [
        r
        for r in releases
        if title_key(core(r.get("title", ""))) == title_key(core(want))
        and version_markers(r.get("title", "")) == version_markers(want)
        and artist_matches(plan.albumartist, r.get("artist-credit", []))
        and (any_count or abs(int(r.get("track-count") or 0) - n) <= 1)
    ]
    return sorted(
        ok,
        key=lambda r: (
            int(r.get("track-count") or 0) != n,
            r.get("status") != "Official",
            _country_rank(r.get("country")),
            r.get("date") or "9999",
            -int(r.get("score", 0)),
        ),
    )


def media_of(release: dict[str, Any], only: int | None = None) -> list[list[dict[str, Any]]]:
    """This release's media as lists of tracks, each carrying its own disc number (§9, slice 146).

    With `only`, that medium first and the rest behind it: a folder called `CD 1` says which disc it
    means and is usually right, but a marker somebody typed is not evidence enough to refuse the
    album when another medium is the one that fits.
    """
    media = [[{**t, "disc": int(m.get("position", n))} for t in m.get("tracks", [])]
             for n, m in enumerate(release.get("media", []), 1)]
    if only is None:
        return media
    mine = [one for one in media if one and one[0]["disc"] == only]
    return mine + [one for one in media if one not in mine]


def match_release_tracks(plan: AlbumPlan, release: dict[str, Any],
                         pinned: bool = False,
                         medium: int | None = None,
                         stored: bool = False) -> dict[int, dict[str, Any]] | None:
    """plan track index -> MB track (with 'disc' added), or None if too few tracks match.

    **A pinned release is only asked about the plan's own tracks** (§9, slice 137). The second test
    — enough of the *release* matched — is there to stop a search pairing a 13-track album with a
    26-track release by accident. A person who named the release has made no accident, and an album
    that holds one disc of two legitimately answers for half of it: `Night is Calling` is 13 files
    against `84dfc64c`'s 13 + 13, which the release-side test rejects by design.
    """
    if medium is not None:
        # **one medium at a time** (§9, slice 146): the files are one disc of a set, so they are
        # matched against one disc and are not asked to account for the whole release.
        for one in media_of(release, only=medium):
            # **a disc is recognised by its length, then by its titles** (§9, slice 146). The share
            # alone is the wrong test both ways: at 80% it seated `ASP/Requiembryo (CD 2)` — seven
            # files that are the *tail* of a 15-track medium — as six tracks of disc 2 and one of
            # disc 1, renumbered; demanding all of them then refused `The Better Life Disc 1`, which
            # really is that 11-track disc with two song names spelled differently. So the medium
            # must be the length of the folder, and then the usual title bar decides.
            if abs(len(one) - len(plan.tracks)) > 1:
                continue
            if (got := _seat(plan, one, pinned=True, stored=stored)) is not None:
                return got
        return None
    mb_tracks = [t for one in media_of(release) for t in one]
    return _seat(plan, mb_tracks, pinned=pinned, stored=stored)


def edits(a: str, b: str) -> int:
    """How many characters would have to change to turn one title into the other."""
    return sum(max(i2 - i1, j2 - j1)
               for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
               if op != "equal")


def near_enough(a: str, b: str) -> bool:
    """Whether two titles differ only as a typo does (§9, slice 149; R-517).

    One character, or a seventh of the longer, whichever is more: `Dunler Ort`/`Dunkler Ort`,
    `Toudion`/`Tourdion`, `Scarazula`/`Scuarazula`, `Zeit zu gehen`/`Zeit zu gehn`, and
    `Meister der LÃ¼gen`/`Meister der Lügen` — which is mojibake in the user's own tags.
    **Containment is deliberately not here**: it took `Milk - Exclusive Track` for `Milk` and
    `Maybe - Remix` for `Maybe (remixed by Dust of Basement)`, which are different recordings, and
    bought five albums for that.
    """
    if not a or not b:
        return False
    return edits(a, b) <= max(1, int(0.15 * max(len(a), len(b))))


def file_seconds(t: PlanTrack) -> float | None:
    """How long this track's file is: measured, else what the stored plan measured, else the video."""
    return t.file_length or getattr(t, "known_length", None) or t.duration or None


def mb_seconds(m: dict[str, Any]) -> float | None:
    """How long MusicBrainz says a release's track is, in seconds."""
    length = m.get("length") or (m.get("recording") or {}).get("length")
    return int(length) / 1000 if length else None


def length_fits(mine: float, m: dict[str, Any]) -> bool:
    """Whether a file of `mine` seconds is the length of this release track (`LENGTH_SLACK`)."""
    theirs = mb_seconds(m)
    return bool(theirs) and abs(theirs - mine) <= LENGTH_SLACK


def _seat(plan: AlbumPlan, mb_tracks: list[dict[str, Any]],
          pinned: bool = False, stored: bool = False) -> dict[int, dict[str, Any]] | None:
    """plan track index -> one of `mb_tracks`, or None if too few fit.

    **The album's own release is recognised by its recordings, whatever the files' lengths**
    (§9, slice 198; q23, R-666): `Mono Inc. — Symphonic Live` holds files cut elsewhere than
    MusicBrainz cuts them (424 s against 282 s), and with the length asked here too only 7 of its 18
    stored recordings held their release, which then did not fit at all. With `stored`, the first
    pass seats a track by its stored recording; where it sits is still `_places`' question.
    """
    unused = list(range(len(mb_tracks)))
    matches: dict[int, dict[str, Any]] = {}
    def pick(t: PlanTrack, among: list[int]) -> int:
        """Of several release tracks that could be this one: the recording it already is, else the
        closest length (§9, slice 164)."""
        if len(among) == 1:
            return among[0]
        already = t.mbid or getattr(t, "known_recording", None)   # the stored plan's, on a fresh read
        mine = file_seconds(t)
        if already and (held := [j for j in among if (mb_tracks[j].get("recording") or {}).get("id") == already]):
            # **unless its length says otherwise** (§9, slice 192; q16): `Mono Inc. — Together Till
            # The End` stored one recording for both `Out in the Fields`, 1-04 (246 s) and 3-04
            # (216 s), and the stored id took the 246-s file to 3-04 and the 216-s one to 1-04
            if not mine or length_fits(mine, mb_tracks[held[0]]) \
                    or not any(length_fits(mine, mb_tracks[j]) for j in among):
                return held[0]
        if not mine:
            return among[0]
        return min(among, key=lambda j: abs(mb_seconds(mb_tracks[j]) - mine)
                   if mb_seconds(mb_tracks[j]) else float("inf"))

    # **the recording a track already is, first of all** (§9, slice 177; R-584): `Das Ich — Lava (Asche)`
    # names its files `Schwarzer Stern` and `Schwarzer Stern (by Terminal Choice)`, the release calls
    # them `(remix)` and `(reworked by Terminal Choice)`, and only the recording ids the plan already
    # held said which was which — a deep read seated them by title and put one past the last track
    knowns = Counter(t.mbid or getattr(t, "known_recording", None) for t in plan.tracks)
    for i, t in enumerate(plan.tracks):
        if not (known := t.mbid or getattr(t, "known_recording", None)):
            continue
        if knowns[known] > 1:
            continue    # one recording stored for two files says nothing about either (slice 192)
        held = [j for j in unused if (mb_tracks[j].get("recording") or {}).get("id") == known]
        if len(held) == 1 and (stored or (mine := file_seconds(t)) is None
                               or length_fits(mine, mb_tracks[held[0]]) or not mb_seconds(mb_tracks[held[0]])):
            matches[i] = mb_tracks[held[0]]
            unused.remove(held[0])
    # **the whole title first** (§9, slice 164): `Life Goes Around (Previously Unreleased)` came before
    # the plain `Life Goes Around` in the plan, its bracket-stripped name matched the release's one
    # `Life Goes Around`, and the plain song was left without a seat. A name that matches whole is
    # seated before any name that only matches without its brackets.
    for i, t in enumerate(plan.tracks):
        if i in matches:
            continue
        # (and the same take by its brackets: `… - Aggro Mix` keys like `… (Aggro mix)`, and whether a
        # dash names a take is a question of its own — queued, not answered here)
        whole = [j for j in unused if title_key(mb_tracks[j]["title"]) == title_key(t.title)
                 and takes(mb_tracks[j]["title"]) == takes(t.title)]
        if whole:
            j = pick(t, whole)
            matches[i] = mb_tracks[j]
            unused.remove(j)
    for i, t in enumerate(plan.tracks):
        if i in matches:
            continue
        want = title_key(core(t.title))
        # **one take is not another** (§9, slice 163): `core` strips the brackets, so the plain
        # `Saigon und Bagdad` was seated on the release's `Saigon und Bagdad (Club Mix)` — the
        # release spells the plain one `Saigon & Bagdad` — and an update renamed the file to the
        # mix's number while the mix's own file held that name
        same = [j for j in unused
                if title_key(core(mb_tracks[j]["title"])) == want and takes(mb_tracks[j]["title"]) == takes(t.title)]
        if not same:
            continue
        # **and one title twice on a release is told apart by what the track already is, then by
        # its length** (§9, slice 164): `Sturm aufs Paradies` has `Orpheus` at 1-09 (294 s) and 2-06
        # (401 s); the first exact name took the 401-s file to 1-09
        j = pick(t, same)
        matches[i] = mb_tracks[j]
        unused.remove(j)
    # **a second pass, and only where nothing else is near** (§9, slice 149). Every exact title is
    # seated first, so a typo can never take a seat an exact name wanted; and a near pair is taken
    # only when it is the one candidate for that file *and* that file is the one candidate for it.
    # `_seats_by_whole_title` records what looseness costs when it is ambiguous: with `core` alone,
    # `Dreams (Deep crowl Mix)` matched the plain `Dreams` and the two were swapped.
    for i, t in enumerate(plan.tracks):
        if i in matches:
            continue
        want = title_key(core(t.title))
        close = [j for j in unused if near_enough(want, title_key(core(mb_tracks[j]["title"])))
                 and takes(mb_tracks[j]["title"]) == takes(t.title)]
        if len(close) != 1:
            continue
        their_title = title_key(core(mb_tracks[close[0]]["title"]))
        rivals = [k for k, other in enumerate(plan.tracks)
                  if k != i and k not in matches
                  and near_enough(title_key(core(other.title)), their_title)]
        if not rivals:
            matches[i] = mb_tracks[close[0]]
            unused.remove(close[0])
    mine = len(matches) >= math.ceil(MIN_TRACK_MATCH * len(plan.tracks))
    theirs = len(matches) >= math.ceil(MIN_TRACK_MATCH * len(mb_tracks))
    enough = mine if pinned else (mine and theirs)
    return matches if mb_tracks and enough else None


def pinned_release(plan: AlbumPlan) -> str | None:
    """The release a person said this album is, or None (§9, slice 137; R-489).

    `mbid` was the one album field no edit could hold: `merge_plans` took the fresh value
    unconditionally and nothing ever wrote a provenance for it, so a hand-set release was gone at the
    next update. The user's own `Night is Calling` is the case — a rip of **disc 1 of a 2-CD
    release**, which the search can only ever answer with the 13-track single-disc edition, because
    a half-present release is exactly what a search is built to reject.
    """
    return plan.mbid if plan.provenance.get("mbid") == Provenance.USER and plan.mbid else None


def carry_the_pin(existing: AlbumPlan, fresh: AlbumPlan) -> str | None:
    """Put the album's pinned release onto the fresh plan that is about to be enriched (slice 137).

    **The pin is on the plan on disk and the enrichment happens on the fresh one.** Found by the real
    run: `DOMINUM — Night is Calling` was pinned to `84dfc64c` and the pass still logged *looking for
    the release* and took the search's answer. `merge_plans` then kept the pinned **id** — so the plan
    said one release while its names, numbers and discs came from another. A pin that only survives
    the merge is not a pin.
    """
    if pin := pinned_release(existing):
        fresh.mbid = pin
        fresh.provenance["mbid"] = Provenance.USER
    return pin


def fit_measure(plan: AlbumPlan, release: dict[str, Any]) -> tuple[int, int, float]:
    """How well a release fits this album, by one measure (§9, slice 177; R-584): how many of the
    titles the plan holds the release has **with the same take**, then how many tracks are the recording they
    already were, then — smaller is better — how far the lengths are apart on average."""
    left = [t for one in media_of(release) for t in one]
    # **a bracket that repeats the album's name is not a take** (§9, slice 198; q23, R-668):
    # `Mono Inc. — Symphonic Live`'s own release titles every track `… (Symphonic Live)`, and read
    # as a take that made it fit none of the plain titles its folder has. The same album-wide rule
    # `drop_album_name` applies after enrichment, asked here of the release's own titles.
    named = dict(zip(map(id, left), without_album_name(release.get("title") or "", [m["title"] for m in left]),
                     strict=True))
    same = recs = 0
    off: list[float] = []
    for t in plan.tracks:
        title = getattr(t, "known_title", None) or t.title     # the plan's own, on a fresh read
        hit = next((m for m in left if title_key(named[id(m)]) == title_key(title)
                    and takes(named[id(m)]) == takes(title)), None)
        if hit is None:
            continue
        left.remove(hit)
        same += 1
        known = t.mbid or getattr(t, "known_recording", None)
        if known and (hit.get("recording") or {}).get("id") == known:
            recs += 1
        if (mine := t.file_length or t.duration) and (theirs := hit.get("length")):
            off.append(abs(mine - int(theirs) / 1000))
    return same, recs, round(sum(off) / len(off), 1) if off else float("inf")



def strictly_better(plan: AlbumPlan, found: dict[str, Any], stored: dict[str, Any]) -> bool:
    """Whether a release a search found fits this album strictly better than the one it already is."""
    theirs, ours = fit_measure(plan, found), fit_measure(plan, stored)
    if ours[0] == len(plan.tracks):
        return False                     # the stored release has every title, with its take
    if theirs[0] != ours[0]:
        return theirs[0] > ours[0]
    if theirs[1] != ours[1]:
        return theirs[1] > ours[1]
    # **and never on the lengths alone** (§9, slice 198; q23, R-666): with the same titles and the
    # same recordings, a closer average measures how complete MusicBrainz' data is, not another
    # edition. `Feuerschwanz — Knightclub`'s stored release has 4 lengths, a later one has all 12.
    return False


def release_rank(cand: dict[str, Any], release: dict[str, Any],
                 matches: dict[int, dict[str, Any]]) -> tuple[Any, ...]:
    """Which of several fitting releases this album is: best first (§9, slice 147).

    **Most of its titles matched**, then an official release over a promo or a bootleg, then the
    earliest date — a reissue is the same record and the first pressing is the one to name. The id
    breaks a remaining tie so the answer does not depend on the order a search happened to return.
    """
    official = (cand.get("status") or release.get("status")) == "Official"
    when = str(cand.get("date") or release.get("date") or "9999")
    return (-len(matches), not official, when, str(cand.get("id") or ""))


def says_offer(one: dict[str, Any]) -> str:
    """One candidate in a line, as the log and the panel both say it (§9, slice 145)."""
    where = ", ".join(x for x in (one.get("date"), one.get("country")) if x)
    shape = "/".join(str(n) for n in one.get("media") or []) or str(one.get("tracks") or "?")
    fit = (f"{one['matched']} of {one['needed']} titles fitted"
           if one.get("matched") is not None else "not opened")
    return f"  {one['id']} {one['title']!r} ({where or 'no date'}) {shape} track(s) — {fit}"


def _fits(plan: AlbumPlan, mb_tracks: list[dict[str, Any]]) -> int:
    """How many of the plan's titles are somewhere in this release — the number a refusal hangs on."""
    left = [title_key(core(t.get("title", ""))) for t in mb_tracks]
    n = 0
    for track in plan.tracks:
        want = title_key(core(track.title))
        if want in left:
            left.remove(want)
            n += 1
    return n


def offered_from(cand: dict[str, Any], release: dict[str, Any] | None = None,
                 matched: int | None = None, needed: int | None = None) -> dict[str, Any]:
    """One candidate as a person needs to see it (§9, slice 145).

    The search's own answer already carries the title, the date, the country and the track count, so
    every candidate can be listed without opening it. The ones that *were* opened carry how many of
    their track titles fitted and how many were needed, which is the whole reason they were refused.
    """
    # **the search's own answer says more than it was being asked for** (§9, slice 157; R-519
    # item 3): the formats, the label, the per-medium track counts and MusicBrainz' own
    # disambiguation are all in it already, so a candidate nobody opened can still say what shape
    # it is and which edition it is. Two `Policy of Truth` singles from one year are told apart by
    # "collector's edition" and "7\" Vinyl", not by their ids.
    opened = [len(m.get("tracks", [])) for m in (release or {}).get("media", [])]
    listed = [int(m.get("track-count") or 0) for m in (cand.get("media") or [])]
    formats = [str(m["format"]) for m in (cand.get("media") or []) if m.get("format")]
    labels = [one["label"]["name"] for one in (cand.get("label-info") or [])
              if (one.get("label") or {}).get("name")]
    media = opened or listed
    return {"id": cand["id"], "title": cand.get("title") or (release or {}).get("title") or "",
            "date": cand.get("date") or (release or {}).get("date") or "",
            "country": cand.get("country") or (release or {}).get("country") or "",
            "tracks": int(cand.get("track-count") or sum(media) or 0),
            "media": media, "matched": matched, "needed": needed,
            "format": " + ".join(dict.fromkeys(formats)),
            "label": labels[0] if labels else "",
            "note": str(cand.get("disambiguation") or (release or {}).get("disambiguation") or "")}


def enrich_release(plan: AlbumPlan, mb: MusicBrainzAPI) -> bool:
    which: int | None = None     # the medium a folder's own disc marker names (§9, slice 146)
    if pinned := pinned_release(plan):
        # **a pin is not a guess, so it is not searched for and not voted on** (slice 137)
        candidates = [{"id": pinned}]
        every = candidates
    else:
        every = release_candidates(plan, mb.search_releases(plan.albumartist, core(plan.album)))
        # **the release this album already is goes first** (§9, slice 177; R-584): it was not a
        # candidate at all, so a deep read let the search's order replace `Catching Up With Depeche
        # Mode` 1985, which has `Somebody (remix)` as the file does, with 1987, which has `Somebody`
        if stored := getattr(plan, "known_release", None):
            every = [{"id": stored, "stored": True}, *[c for c in every if c.get("id") != stored]]
        if not every and (said := disc_in_name(plan.album)):
            # **the folder is one disc of a set** (§9, slice 146): no release is called
            # `Requiembryo (CD 1)`, so ask for `Requiembryo` and let one medium answer.
            bare, which = said
            every = release_candidates(plan, mb.search_releases(plan.albumartist, core(bare)),
                                       title=bare, any_count=True)
        candidates = every[:RELEASE_LOOKUPS]
    looked: dict[str, dict[str, Any]] = {c["id"]: offered_from(c) for c in every}
    # **every candidate is weighed, and the best one wins** (§9, slice 147). It used to take the
    # first that fitted, and the search's own order put an unofficial or a much later edition in
    # front of the release the album actually is — the choice was the sort of the search result.
    fits: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any], dict[int, dict[str, Any]]]] = []
    for cand in candidates:
        release = mb.release(cand["id"])
        if not release:
            continue
        matches = match_release_tracks(plan, release, pinned=bool(pinned), medium=which,
                                       stored=bool(cand.get("stored")))
        flat = [t for one in media_of(release) for t in one]
        looked[cand["id"]] = offered_from(cand, release,
                                          matched=len(matches or {}) or _fits(plan, flat),
                                          needed=math.ceil(MIN_TRACK_MATCH * len(plan.tracks)))
        if matches is not None:
            fits.append((release_rank(cand, release, matches), cand, release, matches))
    if fits:
        _, cand, release, matches = min(fits, key=lambda f: f[0])
        if kept := next((f for f in fits if f[1].get("stored")), None):
            # a search result replaces the stored release only when it fits strictly better, and
            # never when the stored one fits every title with its take
            if kept[1] is not cand and not strictly_better(plan, release, kept[2]):
                _, cand, release, matches = kept
        rg = release.get("release-group") or cand.get("release-group") or {}
        plan.offered = []      # something fitted, so there is nothing left for a person to choose
        _set(plan, "album", release["title"], matched=True)
        # the album belongs to the main artist; guest credits stay on the tracks
        _set(plan, "albumartist", credit_names(release["artist-credit"])[0]
             or credit_phrase(release["artist-credit"]), matched=True)
        if year := (rg.get("first-release-date") or release.get("date") or "")[:4]:
            _set(plan, "year", int(year), matched=True)
        plan.mbid = release["id"]
        # **this release's own front** (§9, slice 141). The group's is whichever edition the archive
        # chose for the group, and for a release somebody named by hand that is the wrong picture.
        # Behind it stays whatever the album already had — its source's own art is this album's,
        # which is worth more as a second try than another edition's — and the group only fills a
        # slot that would otherwise be empty.
        plan.cover_fallback_url = (plan.cover_fallback_url or plan.cover_url
                                   or (f"{CAA}/release-group/{rg['id']}/front-500" if rg.get("id") else None))
        plan.cover_url = caa_release_front(release["id"])
        # **written down, because only MusicBrainz knows it** (§9, slice 144): an album whose release
        # has no front can then reach its group's on any later pass, offline.
        plan.release_group = rg.get("id") or plan.release_group

        next_number = max((int(m["position"]) for m in matches.values()), default=0) + 1
        places, plan.seat_notes, refused = _places(plan, release, matches)
        for i in refused:
            del matches[i]       # not this recording: nothing of it is written either
        for i, t in enumerate(plan.tracks):
            if m := matches.get(i):
                artist, title = move_feat(credit_phrase(m.get("artist-credit") or release["artist-credit"]), m["title"])
                _set(t, "title", title, matched=True)
                _set(t, "artist", artist, matched=True)
                t.mbid = m["recording"]["id"]
                if length := m.get("length") or m["recording"].get("length"):
                    t.mb_length = round(int(length) / 1000, 1)
            if i in places:
                t.disc, t.number = places[i]
            else:
                t.number, next_number = next_number, next_number + 1
        plan.tracks.sort(key=lambda t: (t.disc, t.number))
        return True
    # **nothing fitted, so say what was weighed** (§9, slice 145): `0/0 tracks matched` and silence
    # about nine releases is what made the user think identification only works by luck.
    plan.offered = sorted(looked.values(), key=lambda o: (-(o["matched"] or 0), o["date"] or "9999"))
    return False


def seat_of(t: PlanTrack, plan: AlbumPlan) -> tuple[int, int]:
    """Where this track sits now. **A folder's file says it** (R-375): `Das Ich — Alter Ego`'s plan
    already held `Kingdott` at 16 while its file still said 09. Anything else: the stored plan's seat
    on a fresh read, else its own."""
    if plan.provider == FOLDER:
        return (t.disc, t.number)
    return getattr(t, "known_seat", None) or (t.disc, t.number)


def _places(plan: AlbumPlan, release: dict[str, Any], matches: dict[int, dict[str, Any]]
            ) -> tuple[dict[int, tuple[int, int]], list[str], set[int]]:
    """Where each track goes: `{plan index: (disc, number)}`, the lines that say what stayed, and the
    matches refused as not being that recording (§9, slice 192; q16, R-648).

    **A track moves only onto a seat the release has, and only when it is that track.** It used to
    take whatever position its match had, and a track nothing matched was counted on after the
    release's last: on the share `Das Ich — Alter Ego` put its `09 Kingdott` at 16 of 15,
    `dArtagnan — Felsenfest` one track at 3-19 of a disc of 18, and `Mono Inc. — Together Till The
    End` swapped five pairs of same-titled songs between discs 1 and 3, each file onto the other's
    length. Now a match moves its file when the file is the slot's length (`LENGTH_SLACK`), or when
    its title is the slot's whole title and no other track of the release has that title — a live
    album is minutes off its studio lengths and still the release's `Black Celebration`. A match
    that is neither is refused; a match on the seat it already has is no move and needs neither.
    Whatever is not moved keeps the seat it has, and where a moving
    track would land on a seat somebody keeps, **neither moves** and one line names both.

    Only for an album already in the library, or one whose source states its numbers: a fresh
    playlist has no seat to keep, and its tracks nothing matched are counted on as before.
    """
    flat = [t for one in media_of(release) for t in one]
    names = Counter(title_key(m["title"]) for m in flat)
    keeps = any(getattr(t, "known_seat", None) for t in plan.tracks) or plan.provider == FOLDER
    places: dict[int, tuple[int, int]] = {}
    notes: list[str] = []
    refused: set[int] = set()
    for i, m in matches.items():
        t = plan.tracks[i]
        mine = file_seconds(t)
        whole = title_key(t.title) == title_key(m["title"]) and names[title_key(m["title"])] == 1
        stays = (int(m["disc"]), int(m["position"])) == seat_of(t, plan)    # not a move: nothing to earn
        if stays or (mine and length_fits(mine, m)) or whole:
            places[i] = (int(m["disc"]), int(m["position"]))
            continue
        refused.add(i)
        d, n = seat_of(t, plan)
        theirs = mb_seconds(m)
        notes.append(f"{d}-{n:02d} {t.title}: keeps its seat — the release's {m['disc']}-{int(m['position']):02d} "
                     f"“{m['title']}” is {f'{theirs:.0f} s' if theirs else 'of no known length'}, "
                     f"the file {f'{mine:.0f} s' if mine else 'of no known length'}")
    if not keeps:
        return {i: s for i, s in places.items() if i not in refused}, notes, refused
    kept = {i: seat_of(t, plan) for i, t in enumerate(plan.tracks) if i not in places}
    last = max((int(m["position"]) for m in matches.values()), default=0)
    for n, i in enumerate((i for i in kept if i not in refused), 1):
        if kept[i][1] != last + n:   # where it would have gone: said only where it does not go
            t = plan.tracks[i]
            notes.append(f"{kept[i][0]}-{kept[i][1]:02d} {t.title}: keeps its seat — no track of the release fits it")
    changed = True
    while changed:
        changed = False
        held = {seat: i for i, seat in kept.items()}
        for i, seat in list(places.items()):
            if seat == seat_of(plan.tracks[i], plan) or seat not in held:
                continue
            other = plan.tracks[held[seat]]
            notes.append(f"{seat[0]}-{seat[1]:02d} {other.title} and {seat_of(plan.tracks[i], plan)[0]}-"
                         f"{seat_of(plan.tracks[i], plan)[1]:02d} {plan.tracks[i].title}: neither moves — the release "
                         f"puts the second on {seat[0]}-{seat[1]:02d}, where the first stays")
            del places[i]
            kept[i] = seat_of(plan.tracks[i], plan)
            changed = True
            break
    places.update(kept)
    return places, notes, refused


def _seatable(plan: AlbumPlan, releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Releases this album's files could be seated on: the same album, **at least as long**.

    `release_candidates` asks "is this the same release?" and wants the counts within one, which is
    right for enrichment and wrong here: the user's flattened `Early Years` holds 18 files of a
    release of 22, and that release is exactly the answer to which disc each file is on.
    """
    mine = len(plan.tracks)
    ok = [r for r in releases
          if title_key(core(r.get("title", ""))) == title_key(core(plan.album))
          and version_markers(r.get("title", "")) == version_markers(plan.album)
          and artist_matches(plan.albumartist, r.get("artist-credit", []))
          and int(r.get("track-count") or 0) >= mine]
    return sorted(ok, key=lambda r: (int(r.get("track-count") or 0) - mine,
                                     r.get("status") != "Official", -int(r.get("score", 0))))


def _seats_by_whole_title(plan: AlbumPlan, release: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """plan index -> the release's track, matched on the **whole** title.

    Not `core`, which strips what is in brackets — and on an album of originals and remixes that is
    the whole difference between two tracks. Measured against the real release of `Early Years`:
    with `core`, `Dreams (Deep crowl Mix)` matched the plain `Dreams` and the plain `Dreams` matched
    `Dreams (Deep Growl mix)` — every file matched, every seat unique, and the two were swapped. A
    name that does not match whole is not a seat anybody should write a disc number from.
    """
    tracks = [{**t, "disc": m.get("position", 1)}
              for m in release.get("media", []) for t in m.get("tracks", [])]
    free = list(range(len(tracks)))
    out: dict[int, dict[str, Any]] = {}
    for i, mine in enumerate(plan.tracks):
        want = title_key(mine.title)
        for j in free:
            if title_key(tracks[j]["title"]) == want:
                out[i] = tracks[j]
                free.remove(j)
                break
    return out


def discs_for_duplicates(plan: AlbumPlan, mb: MusicBrainzAPI) -> str:
    """Ask MusicBrainz what an album whose numbers repeat really is (R-423, point 3).

    Only for those albums, one release lookup each, and it changes **one thing**: which disc each
    file is on. Nothing of the owner's values is touched — not a title, not an artist, not a number —
    because a folder states those and this does not know better (R-410, ruling 5). Where every file
    falls on exactly one disc and position of one release, the discs are assigned and the album is
    taken in as the multi-disc set it is; where the match is partial, nothing is assigned and the
    line says what was found. This is the "pre-run that takes half of them out in advance".
    """
    try:
        found = _seatable(plan, mb.search_releases(plan.albumartist, core(plan.album)))
    except MusicBrainzError as e:
        return f"MusicBrainz could not be asked: {e}"
    for cand in found[:RELEASE_LOOKUPS]:
        release = mb.release(cand["id"])
        if not release:
            continue
        media = release.get("media") or []
        where = f"{release['id']}, {release.get('date') or '?'}" \
                + (f", {release['country']}" if release.get("country") else "") \
                + f", {len(media)} medium(s) of " \
                + "/".join(str(len(m.get('tracks') or [])) for m in media)
        matches = _seats_by_whole_title(plan, release)
        if len(matches) != len(plan.tracks):
            missed = [t.title for i, t in enumerate(plan.tracks) if i not in matches]
            return (f"{where} — {len(matches)} of {len(plan.tracks)} file(s) matched by their whole "
                    f"title; nothing assigned. Not matched: {', '.join(missed[:3])}"
                    + (" …" if len(missed) > 3 else ""))
        seats = {(int(m["disc"]), int(m["position"])) for m in matches.values()}
        if len(seats) != len(plan.tracks):
            return f"{where} — two files fall on one position; nothing assigned"
        for i, track in enumerate(plan.tracks):
            track.disc = int(matches[i]["disc"])
        plan.tracks.sort(key=lambda t: (t.disc, t.number))
        refresh_derived(plan)
        discs = sorted({t.disc for t in plan.tracks})
        return (f"{where} — every file matched; discs {discs[0]}–{discs[-1]} assigned "
                f"({', '.join(f'disc {d}: {sum(1 for t in plan.tracks if t.disc == d)} files' for d in discs)})")
    return "no release matched it at MusicBrainz; nothing assigned"


# -- entry point -----------------------------------------------------------------------------


def enrich(plan: AlbumPlan, mb: MusicBrainzAPI, progress: Callable[[str], None] = lambda s: None,
           source: Any = None) -> dict[str, int]:
    """Enrich a fresh plan in place. Network errors degrade to 'no match', never abort."""
    stats = {"release": 0, "tracks": 0, "looked_up": 0}
    try:
        if plan.kind in (Kind.OFFICIAL_ALBUM, Kind.ARTIST_PLAYLIST):
            if pinned := pinned_release(plan):
                # **said, because a pinned release is not what the pass would have chosen** (slice 137)
                progress(f"MusicBrainz: release pinned by you ({pinned})")
            else:
                progress(f"MusicBrainz: looking for the release “{plan.album}”")
            stats["release"] = int(enrich_release(plan, mb))
            for line in getattr(plan, "seat_notes", ()):
                progress(line)      # every seat that stays where a move was offered (slice 192)
            if plan.offered and not stats["release"]:
                # **the number, then the list** (§9, slice 145): `0/0 tracks matched` and silence
                # about nine releases is what made the user think identification works by luck.
                progress(f"MusicBrainz: {len(plan.offered)} release(s) weighed, none fitted — "
                         "pin one to say which this album is")
                for one in plan.offered:
                    progress(says_offer(one))
            if pinned and not stats["release"]:
                progress(f"MusicBrainz: the release you pinned ({pinned}) does not answer for this "
                         "album — nothing taken from it")

        # **a recording the album already has is not asked for again** (§9, slice 193; q17): a fresh
        # read knows no ids, so every deep pass searched `Milk and Alcohol` anew and took whichever
        # recording the search put first — 35084d78 on 2026-10-10, a031426b since. The release
        # lookup above still seats it against a release; this search has no better answer.
        todo = [t for t in plan.tracks if not t.mbid and not getattr(t, "known_recording", None)
                and _worth_looking_up(plan, t)]
        for i, t in enumerate(todo, 1):
            progress(f"MusicBrainz: track {i}/{len(todo)} {t.artist} - {t.title}")
            stats["looked_up"] += 1
            t.recording_asked = True      # what a line about this track may then say (slice 206)
            stats["tracks"] += int(enrich_track(t, mb, source, plan.album))
    except MusicBrainzError as e:
        log.warning("MusicBrainz unavailable, continuing without it: %s", e)
    refresh_derived(plan)
    return stats


def _worth_looking_up(plan: AlbumPlan, t: PlanTrack) -> bool:
    if Provenance.USER in t.provenance.values():
        return False
    if getattr(t, "ask_again", False):
        return True     # its stored recording was longer than the video and goes (§9, slice 206; q29b)
    # YouTube Music data is already good for albums; in compilations MB still fixes spelling
    return Provenance.SOURCE_TITLE in t.provenance.values() or plan.kind == Kind.COMPILATION


# Evidence MusicBrainz does not get to overrule: a value the files themselves carry (§9, slice 53).
# Whoever tagged that collection knew which release they had — the Deluxe Edition, the live
# recording, the remaster — and a lookup that matched *a* release is not better information than
# the one in front of it. MusicBrainz still fills every field the files leave empty.
#
# **The file's and the folder's names are the files' own words too** (§9, slice 153; R-525). Only
# `FILE_TAGS` was here, so a title read off the file name was not firsthand and an unmatched
# per-track lookup replaced it: on `Depeche Mode/Policy of Truth`, which matches no release at all,
# `Kaleid` became `Kaleid (remix)` that way. Somebody who named the file `04 - Kaleid.mp3` said as
# much about this recording as a tag would, and a lookup that found *a* recording of that name has
# not earned the right to argue with it.
FIRSTHAND = (Provenance.FILE_TAGS, Provenance.FILE_NAME, Provenance.FOLDER_NAME)


def _set(obj: AlbumPlan | PlanTrack, name: str, value: Any, matched: bool = False) -> None:
    """Write what MusicBrainz says, unless something better is already there.

    **`matched` says a release was identified** (§9, slice 148; the user: *"normalizing a library may
    become quite a workload otherwise"*). Where one was, the files' own tags no longer outrank it:
    whoever tagged that collection knew which record they had, but a release that has been *matched*
    — by name, by artist and by four fifths of its track titles — is better information than a tag
    nobody has checked since. Where no release was identified, `FIRSTHAND` stands as it was and the
    files keep their values.

    A value the **user** typed is never touched by either path: `merge_plans` decides a field is
    theirs by comparing it with `auto`, and restores it over anything written here (slice 29).
    """
    if not matched and obj.provenance.get(name) in FIRSTHAND:
        # kept where a reset can reach it, so the page can still offer what MusicBrainz said
        obj.auto[name] = value
        return
    setattr(obj, name, value)
    obj.provenance[name] = Provenance.MB
    obj.auto[name] = value
