"""A recording id the apply rewrites is a line of the check (§9, slice 196; q21, R-656).

`would_do` held back `KEEP_IF_PRESENT` (lyrics, the release and the recording id) wherever the file
had a value, on every album. The writer holds them back only on an adopted album: on noaap's own
album it writes the plan's. So a file holding recording X under a plan that says Y was rewritten by
the apply, after a check that named only the plan's `another recording id` and no file.
"""

from __future__ import annotations

from test_incremental import FakeYouTube, opus_template
from test_same_slot import album

from noaap.download import load_plan, run, save_plan, would_do
from noaap.tag import tags_in


def recorded_as(album_dir, template, rec):
    """Track 01 written with recording `rec`, the way an apply writes it."""
    plan = load_plan(album_dir)
    plan.tracks[0].mbid = rec
    run(plan, album_dir, FakeYouTube(template), download=False)
    return load_plan(album_dir)


def test_a_recording_id_the_apply_changes_is_said(album, opus_template):
    _, album_dir = album
    plan = recorded_as(album_dir, opus_template, "rec-x")
    one = plan.tracks[0]
    assert tags_in(album_dir / one.filename)["musicbrainz_trackid"] == "rec-x"
    one.mbid = "rec-y"
    save_plan(plan, album_dir)

    said = would_do(plan, album_dir)

    assert [s for s in said if s.startswith(f"{one.number:02d} would be retagged:")
            and "musicbrainz_trackid “rec-x” → “rec-y”" in s], said
    run(plan, album_dir, FakeYouTube(opus_template), download=False)
    assert tags_in(album_dir / one.filename)["musicbrainz_trackid"] == "rec-y", "what the apply then does"
    assert not would_do(load_plan(album_dir), album_dir), "and then nothing is left"


def test_an_unchanged_recording_id_is_no_line(album, opus_template):
    _, album_dir = album
    plan = recorded_as(album_dir, opus_template, "rec-x")
    assert not would_do(plan, album_dir), would_do(plan, album_dir)


def test_an_adopted_album_keeps_the_file_s_id_and_says_nothing(album, opus_template):
    """The writer keeps it there (`keep_unknown`), so the check must not offer it either."""
    _, album_dir = album
    plan = recorded_as(album_dir, opus_template, "rec-x")
    plan.adopted = True
    plan.tracks[0].mbid = "rec-y"
    save_plan(plan, album_dir)

    assert not [s for s in would_do(plan, album_dir) if "musicbrainz_trackid" in s]
