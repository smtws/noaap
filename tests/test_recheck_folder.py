""""Re-check source" on an album whose source is a folder reads that album again (P122, I-429).

The page sent `fetch` with the folder's path, the server keeps only http(s) addresses, and every one of
the 1,303 adopted albums on the share offered a button that answered 400 "no URLs"."""

import pytest
from test_identify import an_app, collection, settled, the_album


def test_one_album_is_read_again_in_full(collection):
    app = an_app(collection)
    source_id = the_album(app)

    job = settled(app, app.submit("update", {"id": source_id}))

    assert job.state == "done", job.log
    assert job.label == "Update Apocalyptica — Cult"
    assert any(line.startswith("reading Apocalyptica/Cult") for line in job.log), job.log
    assert not any("unchanged" in line and "nothing to do" in line for line in job.log), "read in full"


def test_an_unknown_album_is_refused(collection):
    app = an_app(collection)
    with pytest.raises(ValueError, match="unknown album"):
        app.submit("update", {"id": "/nowhere/at/all"})
