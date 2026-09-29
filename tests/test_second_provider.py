"""A second provider, driven end to end (DESIGN §9, slice 51).

This is the proof of the boundary, not that the refactor compiles. `Shelf` exists only here: it
answers out of memory, its refs are nothing like a video id, its titles mean what they say, and it
has none of YouTube's conventions — no channel, no bot check, no title to parse. If the pipeline
still fetches, plans, downloads, tags, looks up lyrics, updates and prunes through it, then the
provider really is behind an interface.

The last case is the one that matters most: **one album whose tracks come from two providers**,
which is what P48's candidates made expressible and what P51 will actually do.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap import sources
from noaap.config import Config
from noaap.download import load_plan, run, save_plan
from noaap.models import Collection, Entry, Music
from noaap.plan import build_plan
from noaap.recycle import entries as bin_entries
from noaap.service import Service

SHELF = "shelf"


class Shelf:
    """A provider with no conventions at all: refs are file names, titles are already right."""

    name = SHELF

    def __init__(self, cfg: Config = None, cancel: Any = None, template: Path | None = None) -> None:
        self.cfg, self.template = cfg, template or Shelf.template_path
        self.fetched: list[str] = []

    # a class attribute so the registry can build one without arguments
    template_path: Path = Path()

    def capabilities(self) -> frozenset[str]:
        return frozenset({sources.CHANGES})       # no search, no details, and no title conventions

    def handles(self, address: str) -> bool:
        return address.startswith("shelf://")

    def collection(self, address: str) -> Collection:
        self.fetched.append(address)
        return Collection(
            source_url=address, source_id="shelf-1", is_playlist=True, title="A Shelf",
            channel="The Shelf", thumbnail=None, fetched_at="2026-09-28",
            entries=[Entry(video_id=f"side-{n}.flac", position=n, title=f"Track {n}",
                           channel="The Shelf", duration=180.0,
                           music=Music(artist="Somebody", track=f"Track {n}", album="A Shelf"))
                     for n in (1, 2, 3)])

    def audio(self, ref: str, into: Path, choice: str = "best") -> Path:
        into.mkdir(parents=True, exist_ok=True)
        out = into / f"{ref}.opus"
        shutil.copy(self.template, out)
        return out

    def probe(self, ref: str) -> Entry:
        return Entry(video_id=ref, position=1, title=ref, channel="The Shelf", duration=1.0)

    def art(self, address: str) -> bytes:
        raise OSError("a shelf has no cover service")

    def changed(self, address: str) -> dict[str, Any] | None:
        return {"ids": [f"side-{n}.flac" for n in (1, 2, 3)], "modified": None}

    def settings(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def shelf(opus_template, monkeypatch):
    Shelf.template_path = opus_template
    monkeypatch.setitem(sources._MAKERS, SHELF, Shelf)
    sources._MAKERS.setdefault(sources.DEFAULT, lambda cfg, cancel=None: FakeYouTube(opus_template))
    return Shelf(Config(), template=opus_template)


def service_for(tmp_path, source, **kw):
    return Service(Config(musicbrainz=False, lyrics=False), tmp_path, yt=source, log=lambda s: None, **kw)


# -- the whole pipeline, through a provider that is not YouTube ------------------------------------


def test_fetch_plan_download_and_tag_through_a_second_provider(tmp_path, shelf):
    outcome = service_for(tmp_path, shelf).fetch("shelf://music")

    assert outcome.status == "ok", outcome.message
    plan = outcome.plan
    assert plan.provider == SHELF, "the plan records whose it is"
    assert [t.video_id for t in plan.tracks] == ["side-1.flac", "side-2.flac", "side-3.flac"]
    # refs nothing like a video id, and titles taken at their word: no convention was applied
    assert [t.title for t in plan.tracks] == ["Track 1", "Track 2", "Track 3"]
    assert all(t.artist == "Somebody" for t in plan.tracks)
    files = sorted(p.name for p in outcome.album_dir.glob("*.opus"))
    assert len(files) == 3 and all(f.endswith(".opus") for f in files)


def test_each_track_carries_the_provider_that_can_fetch_it(tmp_path, shelf):
    plan = service_for(tmp_path, shelf).fetch("shelf://music").plan
    for track in plan.tracks:
        candidate = track.candidate(track.effective_id)
        assert candidate is not None
        assert candidate.provider == SHELF, "a candidate knows its own provider (§9, slice 50)"


def test_update_uses_the_providers_cheap_check(tmp_path, shelf):
    service = service_for(tmp_path, shelf)
    service.fetch("shelf://music")
    before = len(shelf.fetched)

    service.update_all()

    assert len(shelf.fetched) == before, "nothing changed, so the collection was never read again"


def test_pruning_a_second_providers_album_bins_it_like_any_other(tmp_path, shelf):
    service = service_for(tmp_path, shelf)
    service.fetch("shelf://music")
    album_dir = next(tmp_path.glob("*/*/.ytalbum.json")).parent
    plan = load_plan(album_dir)
    plan.tracks[0].in_source = False
    save_plan(plan, album_dir)

    service.prune(album_dir)

    binned = bin_entries(tmp_path)
    assert len(binned) == 1
    assert binned[0].reason == "no longer in the source playlist"


# -- the case the boundary exists for --------------------------------------------------------------


def test_one_album_can_hold_tracks_from_two_providers(tmp_path, shelf, opus_template):
    """P48 made this expressible; this is the first thing that does it.

    A track's audio comes from its chosen candidate's provider — not the album's — so an album
    fetched from YouTube can take one track's audio off the shelf and still download both.
    """
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder

    # one track's audio is taken from the shelf instead; the rest stay YouTube's
    moved = plan.tracks[0]
    moved.candidates.append(type(moved.candidates[0])(ref="side-9.flac", provider=SHELF,
                                                     added_by="user", why="a better copy on the shelf"))
    moved.source_override = "side-9.flac"
    moved.sync_candidates()
    save_plan(plan, album_dir)

    service = service_for(tmp_path, yt)
    run(plan, album_dir, yt, track_source=service._track_source(plan))

    assert all(t.state == "done" for t in plan.tracks), [t.error for t in plan.tracks if t.error]
    assert "side-9.flac" not in yt.downloads, "the moved track did not come from YouTube"
    assert len(yt.downloads) == len(plan.tracks) - 1
    assert (album_dir / moved.filename).is_file()


def test_a_provider_without_a_capability_is_simply_not_asked(tmp_path, shelf):
    """A shelf cannot search and has no covers. Neither is an error."""
    assert sources.SEARCH not in shelf.capabilities()
    assert not hasattr(shelf, "find")

    outcome = service_for(tmp_path, shelf).fetch("shelf://music")
    assert outcome.status == "ok", "no cover service, and the album is still fine"
    assert not list(outcome.album_dir.glob("cover.*"))


def test_an_unknown_provider_is_refused_rather_than_guessed(tmp_path):
    with pytest.raises(ValueError, match="unknown source provider"):
        sources.get("gramophone", Config())


# -- the guard ------------------------------------------------------------------------------------


# The boundary is only real while nothing outside a provider recognises that provider's anything.
# This is a grep, deliberately: it catches a URL pasted into a message, an id pattern copied into a
# helper, an import added for convenience — none of which a type checker would mind and all of which
# put the core back where it was.
#
# Three provinces, not one (§9, slice 57). The client is shared now, so `yt_dlp` is allowed wherever
# yt-dlp is spoken; each *site's* shapes are allowed only in that site's own files. `ytdlp.py` is in
# the first list and in neither of the others, which is the whole point of it: shared plumbing that
# cannot name a site.
PROVINCES = [
    ("yt-dlp itself", r"\byt_dlp\b",
     {"ytdlp.py", "youtube.py", "sources_youtube.py", "soundcloud.py", "sources_soundcloud.py",
      "patreon.py", "sources_patreon.py"}),
    # `titles.py` *is* YouTube's title conventions — the provider's province, wherever the file
    # happens to sit. What matters is who reaches into it, which the case after this pins.
    ("YouTube", r"youtube\.com|youtu\.be|ytimg|ggpht|googlevideo"
                r"|watch\?v=|playlist\?list=|OLAK5uy|\bparse_video_title\b|\bchannel_artist\b",
     {"youtube.py", "sources_youtube.py", "titles.py"}),
    ("SoundCloud", r"soundcloud\.com|sndcdn|\bscsearch\b|api-v2\.soundcloud",
     {"soundcloud.py", "sources_soundcloud.py"}),
    # Patreon's shapes: its host, a post address, a campaign address, and the ref only it can read.
    # `config.py` names the two *settings* and no shape, which is why it is not in here.
    ("Patreon", r"patreon\.com|patreonusercontent|\bpatreon:(media|video)\b|current_user_can_view",
     {"patreon.py", "sources_patreon.py"}),
]


@pytest.mark.parametrize("who,pattern,allowed", PROVINCES, ids=[p[0] for p in PROVINCES])
def test_a_provider_stays_inside_its_own_files(who, pattern, allowed) -> None:
    import re

    root = Path(__file__).parent.parent / "src" / "noaap"
    shapes = re.compile(pattern)
    offenders = []
    for path in sorted(root.rglob("*.py")) + sorted((root / "webui").glob("*.mjs")):
        if path.name in allowed:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if shapes.search(line) and "THUMB_HOSTS" not in line:
                offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()[:70]}")
    assert not offenders, f"{who} has leaked out of its province:\n" + "\n".join(offenders)


def test_the_shared_client_knows_no_site() -> None:
    """`ytdlp.py` exists to be shared, so it is the one file where a site name would be invisible:
    it is nobody's province and every provider imports it."""
    shared = (Path(__file__).parent.parent / "src" / "noaap" / "ytdlp.py").read_text(encoding="utf-8")

    for who, pattern, _ in PROVINCES[1:]:
        import re
        assert not re.search(pattern, shared), f"{who} reached into the shared client"


def test_what_still_reaches_into_youtubes_title_conventions_is_written_down() -> None:
    """One module in the core still imports `titles`, and it is named here so it cannot become two.

    `plan.py` uses `clean_title`, `drop_label` and `NOISE_WORDS` for **album-name hygiene** —
    "SABATON - Legends (Full Album)" → "Legends". That is a YouTube convention doing a job the core
    needs, and a folder provider will want a different answer, so it belongs behind the boundary
    too. It is not moved here because commit 7 proves the boundary rather than widening it, and
    because the intake-folder package (P51) is what will say what the other answer should be.

    Until then this is the whole of the remaining coupling, and adding to it fails.
    """
    import re

    root = Path(__file__).parent.parent / "src" / "noaap"
    reaching = sorted(p.name for p in root.glob("*.py")
                      if p.name not in {"titles.py", "sources_youtube.py"}
                      and re.search(r"(?m)^from \.titles import", p.read_text(encoding="utf-8")))
    assert reaching == ["plan.py"], f"the coupling grew: {reaching}"


def test_cookies_are_given_to_the_shared_client_never_taken_by_it() -> None:
    """One site's credentials must not be reachable from a helper every site calls. `ytdlp.params`
    takes them as arguments; a provider reads its own config and passes its own (§9, slice 57)."""
    import re

    from noaap import ytdlp

    shared = (Path(__file__).parent.parent / "src" / "noaap" / "ytdlp.py").read_text(encoding="utf-8")
    # the import, not the word: the docstring explains what it must not do, and a scanner that
    # matches its own explanation is the mistake this suite keeps re-learning
    assert not re.search(r"(?m)^\s*(from \.config import|import .*\bconfig\b)", shared), \
        "the shared client imported a config, which is the only way it could read one"

    got = ytdlp.params(cookies_file="/somewhere/cookies.txt")
    assert got["cookiefile"] == "/somewhere/cookies.txt"
    assert "cookiesfrombrowser" not in got, "a file wins; asking a browser as well would be two answers"
    assert ytdlp.params(cookies_from_browser="firefox:Work")["cookiesfrombrowser"] == ("firefox", "Work", None, None)
    assert "cookiefile" not in ytdlp.params(), "no cookies unless somebody hands them over"


def test_the_shared_client_shortens_in_two_steps() -> None:
    """A provider reads the whole message for the phrases it knows, then cuts it. Doing both at
    once in the shared half would hide an age gate behind a first sentence."""
    from noaap import ytdlp

    whole = ytdlp.trim("ERROR: [somewhere] abc123: Video unavailable. This video is private")
    assert whole == "Video unavailable. This video is private"
    assert ytdlp.one_sentence(whole) == "Video unavailable"


# -- a listing nobody shows is a listing nobody has (§9, slice 71) -----------------------------------


def test_a_channel_shows_every_tab_a_provider_mints(tmp_path):
    """`service.channel` grouped by a table of the three tabs YouTube and the folder source use, so
    Patreon's `posts` refs were filtered out of their own listing: the live campaign read came back
    with five posts and the CLI said "this channel has no releases or playlists". Found by running it,
    not by a case — every case so far called `listing()` and never what shows it."""
    from noaap import service
    from noaap.models import SourceRef

    class Owner:
        name = "owner"

        def handles(self, address): return True
        def capabilities(self): return frozenset({sources.LISTING})
        def listing(self, address):
            return [SourceRef(url="u1", source_id="1", title="a post", tab="posts"),
                    SourceRef(url="u2", source_id="2", title="an album", tab="releases"),
                    SourceRef(url="u3", source_id="3", title="something new", tab="zines")]

    svc = service.Service.__new__(service.Service)
    svc.source_for_address = lambda url: Owner()
    svc.log = lambda *a, **k: None
    svc._said = lambda url, source: url

    groups = svc.channel("https://example.test/owner")

    assert [label for label, _ in groups] == ["Releases (official albums and singles)", "Posts", "Zines"]
    assert [r.title for _, refs in groups for r in refs] == ["an album", "a post", "something new"]
