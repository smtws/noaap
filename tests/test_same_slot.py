"""One slot, one file (DESIGN §9, slice 163; R-566, P119).

The update of 2026-10-10 left four albums with a plan that named a file it did not have:
- `Metallica/Metallica`, `Depeche Mode/Memento Mori`: the playlist listed a new video for a song the
  album held; the old entry moved to the end, the new one was downloaded into the name the old file
  still had — overwriting it — and the old entry's rename then carried the new audio away.
- `In Extremo/Kompass zur Sonne`, `Saltatio Mortis/Sturm aufs Paradies`: a rename was skipped because
  the name was taken, and the plan recorded the name anyway.
Two audio templates of different length make an overwrite visible: whose audio a file holds is its
length, not its tags (which are written from the plan).
"""

import copy
import shutil
import subprocess

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from noaap.config import Config
from noaap.download import load_plan, lost_files, run, save_plan
from noaap.enrich import _seat
from noaap.plan import build_plan, refresh_derived
from noaap.service import Service
from noaap.tag import measure


@pytest.fixture(scope="module")
def long_template(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path_factory.mktemp("tpl2") / "t2.opus"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=3", "-c:a", "libopus", str(path)], check=True)
    return path


class Swapping(FakeYouTube):
    """The playlist as it reads now: one entry's video replaced by `new`, whose audio is longer."""

    def __init__(self, template, longer, new_id, title=None, modified="20260202"):
        super().__init__(template)
        self.longer, self.new_id, self.title, self.modified = longer, new_id, title, modified
    def collection(self, url):
        c = vol1()
        # entries[0] is the whole-album video, which is not a track
        c.entries[1].video_id = self.new_id
        if self.title:
            c.entries[1].title = self.title
        c.modified = self.modified
        return c

    def changed(self, url):
        return {"ids": [e.video_id for e in self.collection(url).entries], "modified": self.modified}

    def audio(self, ref, into, choice="best"):
        out = super().audio(ref, into, choice)
        if ref == self.new_id:
            shutil.copy(self.longer, out)
        return out


@pytest.fixture
def album(tmp_path, opus_template):
    collection = vol1()
    collection.modified = "20260101"
    plan = build_plan(collection)
    run(plan, tmp_path / plan.folder, FakeYouTube(opus_template))
    return tmp_path, tmp_path / plan.folder


def seconds(path):
    return round(measure(path)[0] or 0)


def test_a_new_video_for_a_held_song_is_that_songs_candidate(album, opus_template, long_template):
    """Metallica's shape. Nothing is downloaded and nothing is lost: the slot keeps its file, the
    playlist's new video is its identity and a candidate, the old one is where the audio is from."""
    lib, album_dir = album
    before = load_plan(album_dir)
    old_id, old_name = before.tracks[0].video_id, before.tracks[0].filename
    yt = Swapping(opus_template, long_template, "NEWVIDEO001")
    Service(Config(musicbrainz=False, lyrics=False), lib, yt=yt).update_all()
    after = load_plan(album_dir)
    assert len(after.tracks) == len(before.tracks)
    slot = next(t for t in after.tracks if t.number == 1)
    assert (slot.video_id, slot.source_override, slot.in_source) == ("NEWVIDEO001", old_id, True)
    assert {c.ref for c in slot.candidates} >= {"NEWVIDEO001", old_id}
    assert "NEWVIDEO001" not in yt.downloads
    assert slot.filename == old_name and seconds(album_dir / slot.filename) == 1
    assert lost_files(album_dir, after) == []


def test_an_old_file_is_moved_before_a_new_one_takes_its_name(album, opus_template, long_template):
    """The exact sequence of 2026-10-10, below the merge: a finished track moving from 01 to 13, and
    a new track for 01. The old code downloaded into 01 first — over the old file — and then the
    rename carried the new audio to 13."""
    lib, album_dir = album
    plan = load_plan(album_dir)
    old = plan.tracks[0]
    old_name = old.filename
    old.number, old.in_source = 13, False                     # moved to the end by the merge
    new = copy.deepcopy(old)
    new.video_id, new.number, new.state, new.in_source = "NEWVIDEO002", 1, "pending", True
    new.candidates, new.chosen, new.tagged, new.file_length = [], None, None, None
    new.sync_candidates()
    plan.tracks.insert(0, new)
    refresh_derived(plan)
    assert new.filename == old_name                             # the new one wants the old file's name
    yt = Swapping(opus_template, long_template, "NEWVIDEO002")
    run(plan, album_dir, yt)
    after = load_plan(album_dir)
    o = next(t for t in after.tracks if t.video_id == old.video_id)
    n = next(t for t in after.tracks if t.video_id == "NEWVIDEO002")
    assert o.filename != n.filename
    assert seconds(album_dir / o.filename) == 1 and seconds(album_dir / n.filename) == 3
    assert lost_files(album_dir, after) == []


def test_a_rename_onto_a_name_somebody_else_holds_is_not_recorded(album):
    """In Extremo's and Saltatio Mortis' shape: the name is taken by a file that is not moving. The
    file keeps its name and the plan keeps saying so."""
    lib, album_dir = album
    plan = load_plan(album_dir)
    first = plan.tracks[0]
    stray = album_dir / "stray.opus"
    (album_dir / first.filename).rename(stray)          # the file has another name than wanted
    first.filename = "stray.opus"
    other = plan.tracks[1]
    (album_dir / other.filename).rename(album_dir / "held.opus")
    other.filename = "held.opus"
    save_plan(plan, album_dir)
    blocker = album_dir / plan.tracks[0].filename      # not used; kept for clarity
    # the wanted name of the first track is occupied by an unrelated file
    from noaap.plan import wanted_filename
    wanted = wanted_filename(plan, first)
    (album_dir / wanted).write_bytes(b"somebody else's")
    Service(Config(musicbrainz=False, lyrics=False), lib, yt=FakeYouTube(stray)).repair()
    after = load_plan(album_dir)
    mine = next(t for t in after.tracks if t.video_id == first.video_id)
    assert mine.filename == "stray.opus" and (album_dir / "stray.opus").exists()
    assert (album_dir / wanted).read_bytes() == b"somebody else's"
    assert lost_files(album_dir, after) == []
    del blocker


def test_two_tracks_that_trade_names_both_arrive(album):
    lib, album_dir = album
    plan = load_plan(album_dir)
    a, b = plan.tracks[0], plan.tracks[1]
    (album_dir / a.filename).rename(album_dir / "tmp-a")
    (album_dir / b.filename).rename(album_dir / a.filename)
    (album_dir / "tmp-a").rename(album_dir / b.filename)
    a.filename, b.filename = b.filename, a.filename     # each holds the other's wanted name
    a_bytes, b_bytes = (album_dir / a.filename).read_bytes(), (album_dir / b.filename).read_bytes()
    save_plan(plan, album_dir)
    Service(Config(musicbrainz=False, lyrics=False), lib, yt=FakeYouTube(album_dir / a.filename)).repair()
    after = load_plan(album_dir)
    from noaap.plan import wanted_filename
    for t in after.tracks[:2]:
        assert t.filename == wanted_filename(after, t)
    assert lost_files(album_dir, after) == [] and not list(album_dir.glob(".noaap-moving-*"))
    del a_bytes, b_bytes


def test_a_download_never_overwrites_another_tracks_file(album, opus_template):
    lib, album_dir = album
    plan = load_plan(album_dir)
    plan.keep_names = True       # an album that keeps its own names: a pending track's name is not derived
    keeper = plan.tracks[0]
    victim = plan.tracks[1]
    (album_dir / victim.filename).unlink()
    victim.state, victim.filename = "pending", keeper.filename   # a plan that would download onto it
    save_plan(plan, album_dir)
    kept = (album_dir / keeper.filename).read_bytes()
    from noaap.download import run as run_album
    run_album(plan, album_dir, FakeYouTube(opus_template), want=None)
    assert (album_dir / keeper.filename).read_bytes() == kept
    again = load_plan(album_dir)
    v = next(t for t in again.tracks if t.video_id == victim.video_id)
    assert v.state == "failed" and "another track's file" in (v.error or "")


def test_one_take_is_not_another_when_seating_a_release():
    """In Extremo: the release spells the plain song `Saigon & Bagdad`, and `core` made the file's
    `Saigon und Bagdad` equal to the release's `Saigon und Bagdad (Club Mix)`."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:6]
    for t, title in zip(plan.tracks, ("Troja", "Lügenpack", "Biersegen", "Narrenschiff", "Saigon und Bagdad",
                                      "Saigon und Bagdad (Club Mix)")):
        t.title = title
    release = [{"title": "Troja", "position": 1, "disc": 1}, {"title": "Lügenpack", "position": 3, "disc": 1},
               {"title": "Biersegen", "position": 11, "disc": 1}, {"title": "Narrenschiff", "position": 8, "disc": 1},
               {"title": "Saigon & Bagdad", "position": 7, "disc": 1},
               {"title": "Saigon und Bagdad (Club Mix)", "position": 14, "disc": 1}]
    seats = _seat(plan, release, pinned=True)
    assert sorted(seats) == [0, 1, 2, 3, 5] and seats[5]["position"] == 14   # the plain one is not seated


# -- the mend: a plan that names files it does not have (R-566 item 3) ----------------------------


def _folder_album(tmp_path, opus_template, long_template):
    """In Extremo's shape, by hand: `07 - Saigon` (the plain song) and `14 - Saigon (Club Mix)`, after
    an update seated the plain one on the mix and moved the mix to 15."""
    from noaap.download import save_plan
    album_dir = tmp_path / "In Extremo" / "Kompass zur Sonne"
    album_dir.mkdir(parents=True)
    plain, mix = "In Extremo - Kompass zur Sonne - 07 - Saigon und Bagdad.opus", \
        "In Extremo - Kompass zur Sonne - 15 - Saigon und Bagdad (Club Mix).opus"
    shutil.copy(opus_template, album_dir / plain)
    shutil.copy(long_template, album_dir / mix)
    plan = build_plan(vol1())
    plan.provider, plan.keep_names = "folder", True
    one, two = plan.tracks[0], plan.tracks[1]
    one.video_id, one.adopted_name = str(album_dir / plain), plain
    one.adopted_tags = {"title": ["Saigon und Bagdad"], "tracknumber": ["07"]}
    one.title, one.number = "Saigon und Bagdad (Club Mix)", 14
    one.filename = "In Extremo - Kompass zur Sonne - 14 - Saigon und Bagdad (Club Mix).opus"   # not there
    two.video_id = str(album_dir / "In Extremo - Kompass zur Sonne - 14 - Saigon und Bagdad (Club Mix).opus")
    two.adopted_name = "In Extremo - Kompass zur Sonne - 14 - Saigon und Bagdad (Club Mix).opus"
    two.adopted_tags = {"title": ["Saigon und Bagdad (Club Mix)"], "tracknumber": ["14"]}
    two.title, two.number, two.filename = "Saigon und Bagdad (Club Mix)", 15, mix
    plan.tracks = [one, two]
    for t in plan.tracks:
        t.state = "done"
    save_plan(plan, album_dir)
    return album_dir


def test_mend_gives_a_lost_file_back_and_puts_each_take_in_its_own_place(tmp_path, opus_template, long_template):
    from noaap.mend import mend
    album_dir = _folder_album(tmp_path, opus_template, long_template)
    plan = load_plan(album_dir)
    said = mend(plan, album_dir)
    one, two = plan.tracks
    assert (one.title, one.number, one.filename) == ("Saigon und Bagdad", 7, one.adopted_name)
    assert one.mbid is None                                # the recording id was the mix's
    assert (two.title, two.number) == ("Saigon und Bagdad (Club Mix)", 14)
    assert lost_files(album_dir, plan) == [] and len(said) == 3


def test_mend_makes_one_track_of_a_swapped_video_and_says_whose_audio_it_is(album, long_template):
    """Metallica's shape, by hand: the listed video's entry has no file; the old entry holds the only
    copy, and its length is the listed video's."""
    from noaap.mend import mend
    lib, album_dir = album
    plan = load_plan(album_dir)
    old = plan.tracks[0]
    new = copy.deepcopy(old)
    shutil.copy(long_template, album_dir / old.filename)       # what is in the file is the new audio
    old.number, old.in_source, old.duration = 13, False, 1.0
    new.video_id, new.duration, new.candidates, new.chosen = "NEWVIDEO003", 3.0, [], None
    new.filename = "gone.opus"
    new.sync_candidates()
    plan.tracks.insert(0, new)
    said = mend(plan, album_dir)
    mine = [t for t in plan.tracks if (t.title, t.artist) == (old.title, old.artist)]   # a compilation repeats titles
    assert len(mine) == 1
    slot = mine[0]
    assert (slot.video_id, slot.source_override, slot.filename) == ("NEWVIDEO003", None, old.filename)
    assert slot.candidate(old.video_id) is not None
    assert "the file is the listed video's" in said[0]


def test_mend_leaves_a_folder_track_that_is_really_gone(tmp_path, opus_template, long_template):
    from noaap.mend import mend
    album_dir = _folder_album(tmp_path, opus_template, long_template)
    (album_dir / "In Extremo - Kompass zur Sonne - 07 - Saigon und Bagdad.opus").unlink()
    plan = load_plan(album_dir)
    mend(plan, album_dir)
    assert len(plan.tracks) == 2 and len(lost_files(album_dir, plan)) == 1
