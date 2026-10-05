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

import logging
import math
import re
from collections.abc import Callable
from typing import Any

from .download import CAA, caa_release_front
from .mb import MusicBrainzAPI, MusicBrainzError
from .models import AlbumPlan, Kind, PlanTrack, Provenance
from .plan import owner_artist_of, refresh_derived
from .text import key, move_feat

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


def pick_recording(artist: str, title: str, recordings: list[dict[str, Any]]) -> dict[str, Any] | None:
    want, feat = key(core(title)), feat_text(title)
    ok = [r for r in recordings if key(core(r.get("title", ""))) == want and artist_matches(artist, r.get("artist-credit", []))]
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


def enrich_track(t: PlanTrack, mb: MusicBrainzAPI, source: Any = None) -> bool:
    rec = pick_recording(t.artist, t.title, mb.search_recordings(t.artist, core(t.title) or t.title))
    title_source = t.title
    if rec is None:  # maybe "Song - Artist": swap
        swapped_artist, swapped_title = core(t.title), t.artist
        if swapped_artist and swapped_title:
            rec = pick_recording(swapped_artist, swapped_title, mb.search_recordings(swapped_artist, swapped_title))
            title_source = swapped_title
    if rec is None and uploader_stood_in(t, source) and (found := split_lookup(t.title, mb)):
        _, title_source, rec = found
    if rec is None:
        return False

    ac = rec["artist-credit"]
    extra = kept_suffixes(title_source, rec["title"], [r.get("title", "") for r in rec.get("releases", [])])
    title = rec["title"] + extra
    if feat_text(title_source) and len(ac) == 1:  # MB has no guest credit: keep ours
        title += "".join(f" {g}" for g in re.findall(r"\([^)]*feat[^)]*\)", title_source, re.I))
    artist, title = move_feat(credit_phrase(ac), title)  # "A feat. B" - "Song" -> "A" - "Song feat. B"
    _set(t, "artist", artist)
    if extra:
        # "(Live)", "(Behind The Scenes Documentary)": the artist is confirmed, but this is
        # not that recording - keep our title, attach no recording id, and take no length
        # from it either (a live cut measured against the studio one reads as 2 minutes off)
        t.title = t.auto["title"] = title
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
        if key(core(r.get("title", ""))) == key(core(want))
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
                         medium: int | None = None) -> dict[int, dict[str, Any]] | None:
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
            if (got := _seat(plan, one, pinned=True)) is not None:
                return got
        return None
    mb_tracks = [t for one in media_of(release) for t in one]
    return _seat(plan, mb_tracks, pinned=pinned)


def _seat(plan: AlbumPlan, mb_tracks: list[dict[str, Any]],
          pinned: bool = False) -> dict[int, dict[str, Any]] | None:
    """plan track index -> one of `mb_tracks`, or None if too few fit."""
    unused = list(range(len(mb_tracks)))
    matches: dict[int, dict[str, Any]] = {}
    for i, t in enumerate(plan.tracks):
        want = key(core(t.title))
        for j in unused:
            if key(core(mb_tracks[j]["title"])) == want:
                matches[i] = mb_tracks[j]
                unused.remove(j)
                break
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
    left = [key(core(t.get("title", ""))) for t in mb_tracks]
    n = 0
    for track in plan.tracks:
        want = key(core(track.title))
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
    media = [len(m.get("tracks", [])) for m in (release or {}).get("media", [])]
    return {"id": cand["id"], "title": cand.get("title") or (release or {}).get("title") or "",
            "date": cand.get("date") or (release or {}).get("date") or "",
            "country": cand.get("country") or (release or {}).get("country") or "",
            "tracks": int(cand.get("track-count") or sum(media) or 0),
            "media": media, "matched": matched, "needed": needed}


def enrich_release(plan: AlbumPlan, mb: MusicBrainzAPI) -> bool:
    which: int | None = None     # the medium a folder's own disc marker names (§9, slice 146)
    if pinned := pinned_release(plan):
        # **a pin is not a guess, so it is not searched for and not voted on** (slice 137)
        candidates = [{"id": pinned}]
        every = candidates
    else:
        every = release_candidates(plan, mb.search_releases(plan.albumartist, core(plan.album)))
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
        matches = match_release_tracks(plan, release, pinned=bool(pinned), medium=which)
        flat = [t for one in media_of(release) for t in one]
        looked[cand["id"]] = offered_from(cand, release,
                                          matched=len(matches or {}) or _fits(plan, flat),
                                          needed=math.ceil(MIN_TRACK_MATCH * len(plan.tracks)))
        if matches is not None:
            fits.append((release_rank(cand, release, matches), cand, release, matches))
    if fits:
        _, cand, release, matches = min(fits, key=lambda f: f[0])
        rg = release.get("release-group") or cand.get("release-group") or {}
        plan.offered = []      # something fitted, so there is nothing left for a person to choose
        _set(plan, "album", release["title"])
        # the album belongs to the main artist; guest credits stay on the tracks
        _set(plan, "albumartist", credit_names(release["artist-credit"])[0] or credit_phrase(release["artist-credit"]))
        if year := (rg.get("first-release-date") or release.get("date") or "")[:4]:
            _set(plan, "year", int(year))
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
        for i, t in enumerate(plan.tracks):
            if m := matches.get(i):
                artist, title = move_feat(credit_phrase(m.get("artist-credit") or release["artist-credit"]), m["title"])
                _set(t, "title", title)
                _set(t, "artist", artist)
                t.number, t.disc, t.mbid = int(m["position"]), int(m["disc"]), m["recording"]["id"]
                if length := m.get("length") or m["recording"].get("length"):
                    t.mb_length = round(int(length) / 1000, 1)
            else:
                t.number, next_number = next_number, next_number + 1
        plan.tracks.sort(key=lambda t: (t.disc, t.number))
        return True
    # **nothing fitted, so say what was weighed** (§9, slice 145): `0/0 tracks matched` and silence
    # about nine releases is what made the user think identification only works by luck.
    plan.offered = sorted(looked.values(), key=lambda o: (-(o["matched"] or 0), o["date"] or "9999"))
    return False


def _seatable(plan: AlbumPlan, releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Releases this album's files could be seated on: the same album, **at least as long**.

    `release_candidates` asks "is this the same release?" and wants the counts within one, which is
    right for enrichment and wrong here: the user's flattened `Early Years` holds 18 files of a
    release of 22, and that release is exactly the answer to which disc each file is on.
    """
    mine = len(plan.tracks)
    ok = [r for r in releases
          if key(core(r.get("title", ""))) == key(core(plan.album))
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
        want = key(mine.title)
        for j in free:
            if key(tracks[j]["title"]) == want:
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

        todo = [t for t in plan.tracks if not t.mbid and _worth_looking_up(plan, t)]
        for i, t in enumerate(todo, 1):
            progress(f"MusicBrainz: track {i}/{len(todo)} {t.artist} - {t.title}")
            stats["looked_up"] += 1
            stats["tracks"] += int(enrich_track(t, mb, source))
    except MusicBrainzError as e:
        log.warning("MusicBrainz unavailable, continuing without it: %s", e)
    refresh_derived(plan)
    return stats


def _worth_looking_up(plan: AlbumPlan, t: PlanTrack) -> bool:
    if Provenance.USER in t.provenance.values():
        return False
    # YouTube Music data is already good for albums; in compilations MB still fixes spelling
    return Provenance.SOURCE_TITLE in t.provenance.values() or plan.kind == Kind.COMPILATION


# Evidence MusicBrainz does not get to overrule: a value the files themselves carry (§9, slice 53).
# Whoever tagged that collection knew which release they had — the Deluxe Edition, the live
# recording, the remaster — and a lookup that matched *a* release is not better information than
# the one in front of it. MusicBrainz still fills every field the files leave empty.
FIRSTHAND = (Provenance.FILE_TAGS,)


def _set(obj: AlbumPlan | PlanTrack, name: str, value: Any) -> None:
    if obj.provenance.get(name) in FIRSTHAND:
        # kept where a reset can reach it, so the page can still offer what MusicBrainz said
        obj.auto[name] = value
        return
    setattr(obj, name, value)
    obj.provenance[name] = Provenance.MB
    obj.auto[name] = value
