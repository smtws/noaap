"""An lrclib entry that is nearly this recording (DESIGN.md §9, slice 46).

The fixture is the measurement itself: 203 real candidates from this library, each with the two
numbers an alignment produced for it. No model runs here — the decision is a function of those two
numbers, which is exactly why they were recorded.
"""

import json
from pathlib import Path

import pytest

from ytalbum.lyrics import fit_reason, fit_verdict, nominated

MEASURED = json.loads((Path(__file__).parent / "fixtures" / "lrclib_near_misses.json").read_text())["candidates"]


# -- the three outcomes, on the cases that produced them ------------------------------------------


def test_an_entry_that_fits_is_taken_whole():
    # Mr. Hurley & Die Pulveraffen — Blackbeard: the 4.4 s miss that started this
    assert fit_verdict(0.968, 0.0) == "words+stamps"


def test_words_the_aligner_cannot_find_are_not_this_song():
    # Feuerschwanz — Gangnam Style: a title collision, 71% of lines unplaceable
    assert fit_verdict(0.96, 0.71) == "reject"


def test_the_songs_words_on_another_cut_keep_the_words_and_lose_the_stamps():
    # Schandmaul — Kalte Spuren (Live): every line placed, inside 69% of the singing
    assert fit_verdict(0.69, 0.0) == "words"
    # Feuerschwanz — Ringelpietz: a lyric wider than the singing — an 83 s file, a 222 s entry
    assert fit_verdict(1.60, 0.0) == "words"


def test_the_band_around_the_floor_is_decided_by_nobody():
    assert fit_verdict(0.82, 0.0) == "unclear"
    assert fit_verdict(None, 0.0) == "unclear"


@pytest.mark.parametrize(("ours", "theirs", "yes"), [
    (218.1, 213.7, True),      # the near miss: worth an alignment
    (304.8, 288.8, True),      # a live recording 16 s longer: worth one, and it will say so
    (83.1, 222.0, False),      # a clip: no alignment is spent, the panel says what it is
    (239.0, 177.1, False),
])
def test_only_a_plausible_candidate_costs_an_alignment(ours, theirs, yes):
    assert nominated(ours, theirs) is yes


def test_the_two_shapes_of_a_wrong_clock_are_named_apart():
    assert fit_reason(83.1, 222.0) == "a clip"        # our file is much shorter than the song
    assert fit_reason(304.8, 288.8) == "another cut"  # a live version, or a longer edit


# -- the whole measurement, as the library would meet it ------------------------------------------


def decide(row: dict) -> str:
    if not nominated(row["ours"], row["theirs"]):
        return "shown"
    if row["span"] is None:
        return "shown"
    return fit_verdict(row["span"], row["unplaced"])


def test_what_this_library_would_get():
    """The numbers in the report, reproduced from the recordings rather than asserted by hand."""
    counts: dict[str, int] = {}
    for row in MEASURED:
        counts[decide(row)] = counts.get(decide(row), 0) + 1
    assert counts["words+stamps"] == 143     # taken whole, entry and timings
    assert counts["words"] == 16             # the song's words, our own clock
    assert counts["reject"] == 1             # one wrong song in two hundred
    assert counts["unclear"] == 29           # the instrument does not know; the panel shows both numbers
    assert counts["shown"] == 14             # never aligned at all: too far apart to be worth it
    assert sum(counts.values()) == 203


def test_no_beyond_a_quarter_candidate_is_ever_aligned():
    for row in MEASURED:
        if abs(row["ours"] - row["theirs"]) > 0.25 * row["ours"]:
            assert decide(row) == "shown"


def test_every_rejected_entry_is_one_the_aligner_could_not_place():
    rejected = [r for r in MEASURED if decide(r) == "reject"]
    assert all(r["unplaced"] > 0.25 for r in rejected)
    # and nothing with the words in place is ever thrown away: they become "words"
    placed = [r for r in MEASURED if r["span"] is not None and r["unplaced"] <= 0.1]
    assert all(decide(r) in ("words+stamps", "words", "unclear", "shown") for r in placed)


# -- the whole path, with a fake lrclib and a fake aligner ----------------------------------------

from test_incremental import FakeYouTube, opus_template, vol1  # noqa: E402

import ytalbum.service as service_mod  # noqa: E402
from ytalbum.config import Config  # noqa: E402
from ytalbum.download import load_plan, run, save_plan  # noqa: E402
from ytalbum.lyrics import Lyrics  # noqa: E402
from ytalbum.plan import build_plan  # noqa: E402
from ytalbum.service import Service  # noqa: E402
from ytalbum.timing import Timed, TimedLine  # noqa: E402

ENTRY = "[00:10.0] first line\n[00:20.0] second line\n[00:30.0] third line\n"


@pytest.fixture
def album(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    plan = load_plan(album_dir)
    for t in plan.tracks:
        t.lyrics, t.file_length = "none", 200.0
    save_plan(plan, album_dir)
    return album_dir, plan, yt


class FakeLrclib:
    """Answers with one entry, of a length the test chooses."""

    def __init__(self, seconds: float, text: str = ENTRY) -> None:
        self.entry = Lyrics(lrclib_id=42, synced=text, length=seconds)

    def candidates(self, artist, title):
        return [self.entry]

    def by_id(self, lrclib_id):
        return self.entry if lrclib_id == 42 else None


class FakeAligner:
    """Places the words where the test says, and reports the span the test wants."""

    name = "fake"

    def __init__(self, span: float | None, place: int = 3) -> None:
        self.span, self.place = span, place

    def capabilities(self):
        return frozenset({"align"})

    def align(self, audio, lines, **_kw):
        placed = [TimedLine(text=line, start=None if i >= self.place else 11.0 + i * 9)
                  for i, line in enumerate(lines)]
        params = {"own_span": "" if self.span is None else f"{self.span:.3f}"}
        return Timed(lines=placed, provider="local", model="wav2vec2", parameters=params)


def service_for(tmp_path, yt, api, aligner, monkeypatch):
    monkeypatch.setattr(service_mod, "timing_provider", lambda _cfg, _what="": aligner)
    service = Service(Config(), tmp_path, yt=yt, log=lambda s: None)
    service._lrclib = api
    return service


def test_an_entry_that_fits_is_written_with_its_own_timings(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = service_for(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch)
    got = service.check_near_lyrics(plan.source_id, plan.tracks[0].video_id)
    assert got.status == "ok"
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics == "synced" and saved.lyrics_id == 42
    assert saved.provenance.get("lyrics") is None          # lrclib's words stay lrclib's
    assert saved.lyrics_timed_by is None                   # and lrclib's clock stays lrclib's
    assert saved.lyrics_fit["decided"] == "words+stamps"
    assert (album_dir / plan.tracks[0].filename).with_suffix(".lrc").read_text(encoding="utf-8").startswith("[00:10.0]")


def test_another_cut_keeps_the_words_and_takes_our_clock(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = service_for(tmp_path, yt, FakeLrclib(216.0), FakeAligner(0.60), monkeypatch)
    service.check_near_lyrics(plan.source_id, plan.tracks[0].video_id)
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics == "synced" and saved.lyrics_id == 42
    assert saved.lyrics_timed_by == "local/wav2vec2"        # our clock, and it says so
    assert saved.lyrics_fit["decided"] == "words" and saved.lyrics_fit["why"] == "another cut"
    written = (album_dir / plan.tracks[0].filename).with_suffix(".lrc").read_text(encoding="utf-8")
    assert written.startswith("[00:11.0]")                  # the aligner's stamps, not the entry's


def test_a_different_song_is_rejected_and_never_offered_again(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = service_for(tmp_path, yt, FakeLrclib(203.0), FakeAligner(0.95, place=0), monkeypatch)
    service.check_near_lyrics(plan.source_id, plan.tracks[0].video_id)
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics == "none" and 42 in saved.lyrics_rejected
    assert saved.lyrics_fit["decided"] == "reject"
    assert not (album_dir / plan.tracks[0].filename).with_suffix(".lrc").exists()


def test_what_the_instrument_cannot_decide_is_written_down_and_not_taken(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = service_for(tmp_path, yt, FakeLrclib(205.0), FakeAligner(0.80), monkeypatch)
    service.check_near_lyrics(plan.source_id, plan.tracks[0].video_id)
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics == "none"                            # nothing taken
    assert saved.lyrics_fit["decided"] == "unclear"
    assert saved.lyrics_fit["span"] == "0.800" and saved.lyrics_fit["unplaced"] == "0.000"


def test_a_candidate_too_far_away_costs_no_alignment(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    aligned: list[str] = []

    class Counting(FakeAligner):
        def align(self, audio, lines, **kw):
            aligned.append(audio.name)
            return super().align(audio, lines, **kw)

    service = service_for(tmp_path, yt, FakeLrclib(420.0), Counting(0.95), monkeypatch)
    service.check_near_lyrics(plan.source_id, plan.tracks[0].video_id)
    saved = load_plan(album_dir).tracks[0]
    assert aligned == []                                     # the expensive test was never run
    assert saved.lyrics_fit["decided"] == "shown"
    assert saved.lyrics_fit["why"] == "a clip"   # a 200 s file against a 420 s entry


def test_taking_the_words_by_hand_leaves_them_lrclibs(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = service_for(tmp_path, yt, FakeLrclib(205.0), FakeAligner(0.80), monkeypatch)
    service.check_near_lyrics(plan.source_id, plan.tracks[0].video_id)
    got = service.take_plain_lyrics(plan.source_id, plan.tracks[0].video_id, 42)
    assert got.status == "ok"
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics == "plain" and saved.lyrics_id == 42
    assert saved.provenance.get("lyrics") is None            # a human pressing a button changes nothing
    written = (album_dir / plan.tracks[0].filename).with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[00:10.0]" not in written and "first line" in written


# -- the library-wide pass (P39) -------------------------------------------------------------------


def pass_service(tmp_path, yt, api, aligner, monkeypatch, can_align=True):
    service = service_for(tmp_path, yt, api, aligner, monkeypatch)
    monkeypatch.setattr(service_mod, "capabilities_of",
                        lambda _cfg: frozenset({"align"} if can_align else set()))
    return service


def logged(service):
    lines: list[str] = []
    service.log = lines.append
    return lines


def test_the_pass_takes_only_tracks_with_no_words_a_length_and_no_verdict(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    plan.tracks[0].lyrics = "synced"                       # has words already
    plan.tracks[1].file_length = None                      # nothing to measure against
    plan.tracks[2].lyrics_fit = {"decided": "words+stamps"}  # already settled
    plan.tracks[3].lyrics_fit = {"decided": "unclear"}     # settled, but only just
    save_plan(plan, album_dir)
    eligible = len(plan.tracks) - 4

    # a dry run so that selection is all that is under test: a real pass would write words and
    # change what the second half is allowed to see
    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch)
    lines = logged(service)
    service.check_near_lyrics_all(dry_run=True)
    assert f"{eligible} track(s) with no words" in lines[0]

    # --refetch reaches the one that decided nothing, and still not the one that took the words
    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch)
    lines = logged(service)
    service.check_near_lyrics_all(refetch=True, dry_run=True)
    assert f"{eligible + 1} track(s) with no words" in lines[0]


def test_a_dry_run_looks_up_counts_and_writes_nothing(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    before = (album_dir / ".ytalbum.json").read_bytes()
    aligner = FakeAligner(0.95)
    service = pass_service(tmp_path, yt, FakeLrclib(204.4), aligner, monkeypatch)
    lines = logged(service)

    service.check_near_lyrics_all(dry_run=True)

    assert (album_dir / ".ytalbum.json").read_bytes() == before, "a dry run wrote to the plan"
    assert not any((album_dir / f).suffix == ".lrc" for f in [p.name for p in album_dir.iterdir()])
    said = "\n".join(lines)
    assert "nothing is aligned and nothing is written" in said
    assert "alignment(s) would run" in said
    assert "would align" in said


def test_a_dry_run_separates_the_ones_too_far_to_be_worth_an_alignment(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    # a 200 s file against a 600 s entry: outside the nomination guard, so no alignment is spent
    service = pass_service(tmp_path, yt, FakeLrclib(600.0), FakeAligner(0.95), monkeypatch)
    lines = logged(service)
    service.check_near_lyrics_all(dry_run=True)
    said = "\n".join(lines)
    assert "no alignment" in said
    assert "0 alignment(s) would run" in said


def test_the_pass_refuses_when_the_align_slot_cannot_align(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch, can_align=False)
    lines = logged(service)
    outcomes = service.check_near_lyrics_all()
    assert [o.status for o in outcomes] == ["failed"]
    assert "needs a timing provider that can align" in outcomes[0].message
    assert any("timing_align_provider" in line for line in lines)


def test_a_dry_run_needs_no_provider_at_all(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch, can_align=False)
    lines = logged(service)
    service.check_near_lyrics_all(dry_run=True)
    assert any("alignment(s) would run" in line for line in lines), "a dry run aligns nothing, so it may run"


def test_the_summary_counts_by_verdict(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch)
    lines = logged(service)
    service.check_near_lyrics_all()
    summary = next(line for line in lines if line.startswith("near misses: ") and "track(s)" not in line)
    assert "words+stamps" in summary
    assert str(len(plan.tracks)) in summary


def test_the_pass_gives_the_card_back_even_when_a_track_fails(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    released = []
    monkeypatch.setattr(service_mod, "release_gpu_memory", lambda: released.append(True))

    class Exploding(FakeAligner):
        def align(self, audio, lines, **kw):
            raise RuntimeError("the card fell out")

    service = pass_service(tmp_path, yt, FakeLrclib(204.4), Exploding(0.95), monkeypatch)
    logged(service)
    service.check_near_lyrics_all()
    assert released, "the models are released whatever the pass ran into"


# -- remembering that there is nothing to find (P40) -----------------------------------------------


class NoEntries:
    """An lrclib with nothing for this title, which is the commonest answer of all."""

    def candidates(self, artist, title):
        return []

    def by_id(self, lrclib_id):
        return None


def test_no_candidate_is_remembered_and_not_asked_again(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    service = pass_service(tmp_path, yt, NoEntries(), FakeAligner(0.95), monkeypatch)
    logged(service)
    service.check_near_lyrics_all()

    after = load_plan(album_dir)
    assert all(t.lyrics_no_entry for t in after.tracks), "the pass did not write down what it learned"
    assert all(t.lyrics_fit is None for t in after.tracks), "no entry is not a verdict"

    # the next pass has nothing left to look at
    service = pass_service(tmp_path, yt, NoEntries(), FakeAligner(0.95), monkeypatch)
    lines = logged(service)
    service.check_near_lyrics_all()
    assert lines[0] == "near misses: nothing to check"


def test_refetch_asks_again_for_a_track_lrclib_had_nothing_for(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    for t in plan.tracks:
        t.lyrics_no_entry = "2026-09-28"
    save_plan(plan, album_dir)

    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch)
    lines = logged(service)
    service.check_near_lyrics_all(refetch=True, dry_run=True)
    assert f"{len(plan.tracks)} track(s) with no words" in lines[0]


def test_an_entry_appearing_later_clears_the_note(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    for t in plan.tracks:
        t.lyrics_no_entry = "2026-09-28"
    save_plan(plan, album_dir)

    service = pass_service(tmp_path, yt, FakeLrclib(204.4), FakeAligner(0.95), monkeypatch)
    logged(service)
    service.check_near_lyrics_all(refetch=True)

    after = load_plan(album_dir)
    assert all(t.lyrics_no_entry is None for t in after.tracks), "lrclib answered; the note is stale"


def test_the_panel_is_told_nothing_about_it(album, tmp_path, monkeypatch):
    """`lyrics_no_entry` is not a verdict, so it must not reach the lyrics panel at all: `nearMiss`
    renders any unknown `decided` as the unclear wording, which would be a claim about an entry that
    does not exist."""
    album_dir, plan, yt = album
    service = pass_service(tmp_path, yt, NoEntries(), FakeAligner(0.95), monkeypatch)
    logged(service)
    service.check_near_lyrics_all()

    after = load_plan(album_dir)
    assert all(t.lyrics_fit is None for t in after.tracks)
    assert all(t.lyrics_no_entry for t in after.tracks), "it is on the plan"

    # and the panel's payload does not carry it: App.lyrics builds that dict by hand, so this asserts
    # against the real builder rather than against the plan it reads from
    from ytalbum.web import App

    app = App(Config(), tmp_path)
    got = app.lyrics(after.source_id, after.tracks[0].video_id)
    assert got is not None
    assert not [k for k in got if "no_entry" in k], f"the panel is shown {sorted(got)}"


# -- finding the tracks that wait for a person (P41) ------------------------------------------------


def test_needs_you_is_the_two_verdicts_that_decide_nothing():
    from ytalbum.lyrics import needs_you
    from ytalbum.models import PlanTrack

    def track(decided, lyrics="none"):
        return PlanTrack(video_id="v", number=1, filename="f.opus", provenance={},
                         title="t", artist="a", lyrics=lyrics,
                         lyrics_fit={"decided": decided} if decided else None)

    assert needs_you(track("unclear"))
    assert needs_you(track("shown"))
    for decided in ("words+stamps", "words", "reject", "words by hand"):
        assert not needs_you(track(decided)), decided
    assert not needs_you(track(None)), "never checked is not waiting"
    # a sidecar that arrived after the check settles it, whatever the old verdict says
    assert not needs_you(track("unclear", lyrics="synced"))


def test_the_album_row_counts_the_tracks_that_wait(album, tmp_path, monkeypatch):
    from ytalbum.web import App

    album_dir, plan, yt = album
    plan.tracks[0].lyrics_fit = {"decided": "unclear"}
    plan.tracks[1].lyrics_fit = {"decided": "shown"}
    plan.tracks[2].lyrics_fit = {"decided": "words+stamps"}
    plan.tracks[3].lyrics_fit = {"decided": "unclear"}
    plan.tracks[3].lyrics = "synced"          # decided since, by a sidecar
    save_plan(plan, album_dir)

    rows = App(Config(), tmp_path).state()["albums"]
    assert [r["needs_you"] for r in rows] == [2]
