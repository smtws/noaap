"""A title track's uploads are found although the song's name is the album's (§9, slice 214; q37, R-759).

`Limp Bizkit — Gold Cobra` 1-03 `Gold Cobra`: `complete.score` took the album's name out of every
hit title before comparing it with the slot's, and on a title track that left nothing — `recording`
found "nothing close enough" while YouTube has the band's own 234-s upload. The hits are YouTube's
for `ytsearch8:Limp Bizkit - Gold Cobra`, read on 2026-10-16.
"""

from __future__ import annotations

from noaap.complete import Slot, rank, score

SLOT = Slot(disc=1, number=3, title="Gold Cobra", artist="Limp Bizkit", recording="ea3e1d94", length=233.0)
HITS = [
    {"ref": "_i_qxQztHRI", "title": "Limp Bizkit - Gold Cobra", "channel": "limpbizkit", "length": 285},
    {"ref": "5mYRSEv7ixQ", "title": "Limp Bizkit   Gold Cobra", "channel": "CDV222", "length": 235},
    {"ref": "tvLeSwIh71A", "title": "Limp Bizkit - Gold Cobra [Gold Cobra 2011 HD-HQ]", "channel": "spikedization",
     "length": 235},
    {"ref": "F2akndYJGwc", "title": "Limp Bizkit - Gold Cobra (Live at Budapest, Hungary, 2015) [Official Pro Shot]",
     "channel": "Limp Bizkit Brasil", "length": 290},
    {"ref": "KvKTXVzhmX4", "title": "Limp Bizkit - Gold Cobra lyrics", "channel": "Giannis Mavrosavvidis", "length": 237},
    {"ref": "92GiuMyC3G0", "title": "Gold Cobra", "channel": "limpbizkit", "length": 234},
]


def test_the_bands_own_upload_of_the_title_track_scores():
    hit = next(h for h in HITS if h["ref"] == "92GiuMyC3G0")
    assert score(SLOT, hit, "Gold Cobra") == 3.0 + 2.0 + 1.0     # its title, within 10 s, the band's channel


def test_the_full_length_uploads_rank_first_and_the_live_take_not_at_all():
    ranked = [h["ref"] for h in rank(SLOT, HITS, "Gold Cobra")]
    assert ranked[0] == "92GiuMyC3G0"
    assert "5mYRSEv7ixQ" in ranked
    assert "F2akndYJGwc" not in ranked, "another version: live"


def test_on_any_other_track_the_album_name_is_still_taken_out():
    """Letzte Instanz — Heilig, R-563's proof: `Letzte Instanz   Heilig   06   Fur Dich`."""
    slot = Slot(disc=1, number=6, title="Für Dich", artist="Letzte Instanz", length=256.0)
    real = {"ref": "sA5e3HLPyWI", "title": "Letzte Instanz   Heilig   06   Fur Dich",
            "channel": "IronClad Metal & Rock Music", "length": 256}
    assert score(slot, real, "Heilig") == 2.0 + 2.0
