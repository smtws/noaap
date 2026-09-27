"""The two paid providers, offline: fake servers speaking the shapes their documentation describes.

**No request in this test suite has ever been billed.** Every response comes from
`tests/fixtures/*.json`, which are the vendors' documented shapes with plausible values, and every
client is pointed at a local `http.server`. A live test per vendor exists and is skipped unless a
key is in the environment, which it will not be unless someone puts a free-tier key there.
"""

import contextlib
import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.plan import build_plan
from ytalbum.service import Service
from ytalbum.timing import ALIGN, TRANSCRIBE, TimingUnavailable, capabilities_of, provider
from ytalbum.timing_cloud import DeepgramTiming, ElevenLabsTiming

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class Recorder:
    """A vendor-shaped server: it remembers what it was sent and answers what the docs say."""

    def __init__(self, answers: dict[str, tuple[int, dict]]) -> None:
        self.answers = answers
        self.requests: list[dict] = []
        handler = _handler(self)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _handler(recorder: Recorder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            path = self.path.split("?")[0]
            recorder.requests.append({"path": path, "query": self.path, "body": body,
                                      "headers": dict(self.headers)})
            status, answer = recorder.answers.get(path, (404, {"error": "no such path"}))
            data = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


@pytest.fixture
def audio(tmp_path, opus_template):
    path = tmp_path / "track.opus"
    shutil.copy(opus_template, path)
    return path


# -- capabilities, honestly ---------------------------------------------------------------


def test_a_vendor_without_a_key_offers_nothing():
    assert capabilities_of(Config(timing_provider="elevenlabs")) == frozenset()
    assert capabilities_of(Config(timing_provider="deepgram")) == frozenset()
    with pytest.raises(TimingUnavailable, match="no API key"):
        ElevenLabsTiming("").align(Path("x.opus"), ["one"])


def test_each_vendor_claims_only_what_it_sells():
    assert capabilities_of(Config(timing_provider="elevenlabs", timing_elevenlabs_key="k")) == {ALIGN, TRANSCRIBE}
    # Deepgram transcribes; it does not align words you give it, and never says it does
    assert capabilities_of(Config(timing_provider="deepgram", timing_deepgram_key="k")) == {TRANSCRIBE}
    with pytest.raises(TimingUnavailable, match="does not align"):
        DeepgramTiming("k").align(Path("x.opus"), ["one"])
    assert provider(Config(timing_provider="deepgram", timing_deepgram_key="k")).name == "deepgram"


# -- ElevenLabs ---------------------------------------------------------------------------


def test_elevenlabs_aligns_the_lines_it_was_given(audio):
    server = Recorder({"/v1/forced-alignment": (200, fixture("elevenlabs_forced_alignment"))})
    try:
        client = ElevenLabsTiming("secret-key")
        client.BASE = f"{server.url}/v1"
        timed = client.align(audio, ["Berzerkermode ON", "Muskeln, Schweiß und Bärte"], language="de")
    finally:
        server.close()

    assert [line.start for line in timed.lines] == [12.52, 31.06]
    assert timed.provider == "elevenlabs" and timed.unplaced == []
    sent = server.requests[0]
    assert sent["headers"]["xi-api-key"] == "secret-key"
    assert b"Berzerkermode ON Muskeln" in sent["body"]  # the words, as one text, as documented


def test_elevenlabs_leaves_a_line_it_cannot_find_unplaced(audio):
    answer = fixture("elevenlabs_forced_alignment")
    answer["words"] = answer["words"][:2]  # it came back with the first line only
    server = Recorder({"/v1/forced-alignment": (200, answer)})
    try:
        client = ElevenLabsTiming("k")
        client.BASE = f"{server.url}/v1"
        timed = client.align(audio, ["Berzerkermode ON", "Muskeln, Schweiß und Bärte"])
    finally:
        server.close()
    assert timed.lines[0].start == 12.52
    assert timed.unplaced == [1]  # not a guess, and not an exception


def test_elevenlabs_transcribes_words_and_leaves_out_the_noise(audio):
    server = Recorder({"/v1/speech-to-text": (200, fixture("elevenlabs_speech_to_text"))})
    try:
        client = ElevenLabsTiming("k")
        client.BASE = f"{server.url}/v1"
        timed = client.transcribe(audio, language="de")
    finally:
        server.close()
    text = "\n".join(line.text for line in timed.lines)
    assert "Eins zwei drei." in text and "Vier fünf." in text
    assert "(applause)" not in text and "  " not in text  # audio events and spacing are not lyrics
    assert timed.lines[0].start == 0.5
    assert b'name="language_code"' in server.requests[0]["body"]


# -- Deepgram -----------------------------------------------------------------------------


def test_deepgram_uses_its_own_sentences_when_it_sends_them(audio):
    server = Recorder({"/v1/listen": (200, fixture("deepgram_listen"))})
    try:
        client = DeepgramTiming("dg-key")
        client.URL = f"{server.url}/v1/listen"
        timed = client.transcribe(audio, language="en")
    finally:
        server.close()
    assert [line.text for line in timed.lines] == ["One two three.", "Four five."]
    assert [line.start for line in timed.lines] == [0.5, 2.4]
    assert timed.parameters["grouped_by"] == "sentences"
    sent = server.requests[0]
    assert sent["headers"]["Authorization"] == "Token dg-key"
    assert "language=en" in sent["query"] and "model=nova-3" in sent["query"]
    assert sent["body"] == audio.read_bytes()  # the file on disk, raw, as the docs describe


def test_deepgram_groups_the_words_itself_when_it_does_not(audio):
    server = Recorder({"/v1/listen": (200, fixture("deepgram_listen_no_paragraphs"))})
    try:
        client = DeepgramTiming("k")
        client.URL = f"{server.url}/v1/listen"
        timed = client.transcribe(audio)
    finally:
        server.close()
    assert timed.parameters["grouped_by"] == "words"
    assert timed.lines[0].text.startswith("one two three.")  # broken at the punctuation
    assert timed.lines[0].start == 0.5


# -- what they say when they refuse --------------------------------------------------------


@pytest.mark.parametrize(("status", "body", "expected"), [
    (401, fixture("vendor_errors")["elevenlabs_401"], "refused the API key"),
    (429, fixture("vendor_errors")["deepgram_429"], "rate-limiting"),
    (500, {"message": "upstream exploded"}, "refused the request"),
])
def test_a_refusal_carries_the_vendors_own_words(audio, status, body, expected):
    server = Recorder({"/v1/speech-to-text": (status, body)})
    try:
        client = ElevenLabsTiming("k")
        client.BASE = f"{server.url}/v1"
        with pytest.raises(TimingUnavailable, match=expected):
            client.transcribe(audio)
    finally:
        server.close()
    assert len(server.requests) == 1  # never retried: a second request is a second invoice


def test_an_endpoint_that_is_not_there_is_not_a_crash(audio):
    client = ElevenLabsTiming("k")
    client.BASE = "http://127.0.0.1:1/v1"
    with pytest.raises(TimingUnavailable, match="could not be reached"):
        client.transcribe(audio)


# -- through the service, and what it says about it ------------------------------------------


@pytest.fixture
def album(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    return album_dir, load_plan(album_dir), yt


def service_with(tmp_path, yt, engine, monkeypatch, cfg=None):
    import ytalbum.service as service_mod

    monkeypatch.setattr(service_mod, "timing_provider", lambda _cfg, _what="": engine)
    lines: list[str] = []
    service = Service(cfg or Config(), tmp_path, yt=yt, log=lines.append)
    return service, lines


class FakeVendor:
    name = "fakevendor"

    def __init__(self) -> None:
        self.log = lambda _: None

    def capabilities(self):
        return frozenset({TRANSCRIBE})

    def transcribe(self, audio, *, language=None, check=None):
        from ytalbum.timing import Timed, TimedLine

        return Timed(lines=[TimedLine(text="one two", start=1.5), TimedLine(text="three", start=None)],
                     provider=self.name, model="ears", version="1")

    def align(self, audio, lines, *, language=None, check=None):
        raise TimingUnavailable("the fake vendor does not align")


def test_a_draft_is_offered_only_where_there_are_no_words(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    track = plan.tracks[0]
    service, log = service_with(tmp_path, yt, FakeVendor(), monkeypatch)

    result = service.draft_lyrics(plan.source_id, track.video_id)
    assert result["text"] == "one two\nthree"
    assert result["by"] == "fakevendor/ears 1" and result["lines"] == 2 and result["placed"] == 1
    assert any("min of audio" in line for line in log)  # the minutes a request costs are said

    track.lyrics = "plain"
    save_plan(plan, album_dir)
    with pytest.raises(ValueError, match="already has words"):
        service.draft_lyrics(plan.source_id, track.video_id)


def test_a_draft_writes_nothing_until_it_is_saved(album, tmp_path, monkeypatch):
    album_dir, plan, yt = album
    track = plan.tracks[0]
    service, _ = service_with(tmp_path, yt, FakeVendor(), monkeypatch)
    before = sorted((p.name, p.stat().st_mtime_ns) for p in album_dir.iterdir())
    service.draft_lyrics(plan.source_id, track.video_id)
    assert sorted((p.name, p.stat().st_mtime_ns) for p in album_dir.iterdir()) == before

    service.save_lyrics(plan.source_id, track.video_id, "[00:01.5] one two\nthree",
                        timed_by="fakevendor/ears", words_by="fakevendor/ears")
    saved = load_plan(album_dir).tracks[0]
    assert saved.lyrics_words_by == "fakevendor/ears" and saved.lyrics_timed_by == "fakevendor/ears"
    assert saved.provenance["lyrics"] == "user"

    service.save_lyrics(plan.source_id, track.video_id, "words I wrote myself")
    assert load_plan(album_dir).tracks[0].lyrics_words_by is None


def test_a_key_never_reaches_the_page_or_the_log(album, tmp_path, monkeypatch):
    from ytalbum.web import App

    album_dir, plan, yt = album
    cfg = Config(timing_provider="elevenlabs", timing_elevenlabs_key="super-secret")
    app = App(cfg, tmp_path)
    settings = app.settings()
    assert settings["timing"]["keys"] == {"elevenlabs": True, "deepgram": False}
    assert "super-secret" not in json.dumps(settings)
    # per capability now (§9.40): one provider in both slots answers for both
    assert settings["timing"]["price"]["align"][0].startswith("$")  # the list price, with its date
    assert settings["timing"]["price"]["transcribe"][0].startswith("$")
    assert settings["timing"]["sends_audio"] == {"align": True, "transcribe": True}

    service, log = service_with(tmp_path, yt, FakeVendor(), monkeypatch, cfg)
    service.draft_lyrics(plan.source_id, plan.tracks[0].video_id)
    assert "super-secret" not in "\n".join(log)


# -- the real thing, only when a key is deliberately put in the environment --------------------


@pytest.mark.skipif(not os.environ.get("YTALBUM_ELEVENLABS_KEY"),
                    reason="set YTALBUM_ELEVENLABS_KEY to run one real (billed) ElevenLabs request")
def test_elevenlabs_for_real(audio):
    timed = ElevenLabsTiming(os.environ["YTALBUM_ELEVENLABS_KEY"]).align(audio, ["hello"])
    assert timed.provider == "elevenlabs"


@pytest.mark.skipif(not os.environ.get("YTALBUM_DEEPGRAM_KEY"),
                    reason="set YTALBUM_DEEPGRAM_KEY to run one real (billed) Deepgram request")
def test_deepgram_for_real(audio, capsys):
    """One request, deliberately. `YTALBUM_LIVE_AUDIO` points it at something with words in it.

    The default fixture is a one-second sine tone, which proves the key and the auth header and
    nothing about the response's shape; a real track answers whether the documented fields hold.
    What is printed is the *shape* — field names and counts — never the transcript.
    """
    import httpx

    path = Path(os.environ.get("YTALBUM_LIVE_AUDIO") or audio)
    client = DeepgramTiming(os.environ["YTALBUM_DEEPGRAM_KEY"])
    raw: dict = {}
    original = httpx.post

    def watching(*args, **kwargs):  # the one request, kept so its shape can be reported
        response = original(*args, **kwargs)
        with contextlib.suppress(ValueError):
            raw.update(response.json())
        return response

    httpx.post = watching
    try:
        started = time.perf_counter()
        timed = client.transcribe(path)
        seconds = time.perf_counter() - started
    finally:
        httpx.post = original

    assert timed.provider == "deepgram"
    alt = (((raw.get("results") or {}).get("channels") or [{}])[0].get("alternatives") or [{}])[0]
    words = alt.get("words") or []
    paragraphs = (alt.get("paragraphs") or {}).get("paragraphs")
    with capsys.disabled():
        print(f"\n  live Deepgram: {seconds:.1f}s for {path.name}")
        print(f"  top-level keys: {sorted(raw)}")
        print(f"  alternative keys: {sorted(alt)}")
        print(f"  words: {len(words)}; first word keys: {sorted(words[0]) if words else '—'}")
        print(f"  paragraphs present: {paragraphs is not None}; sentences: "
              f"{sum(len(p.get('sentences') or []) for p in (paragraphs or []))}")
        print(f"  lines built: {len(timed.lines)}; grouped_by={timed.parameters.get('grouped_by')}")
        if words:
            w = words[0]
            print(f"  first word start/end: {w.get('start')} / {w.get('end')} "
                  f"(seconds: {isinstance(w.get('start'), (int, float))})")


def test_the_settings_answer_per_slot(tmp_path):
    """With one provider aligning and another drafting, every answer has to name the right one."""
    from ytalbum.web import App

    cfg = Config(timing_align_provider="local", timing_draft_provider="deepgram",
                 timing_deepgram_key="secret-two")
    settings = App(cfg, tmp_path).settings()["timing"]
    assert settings["align_provider"] == "local" and settings["draft_provider"] == "deepgram"
    # the audio leaves the machine for a draft and never for an alignment
    assert settings["sends_audio"] == {"align": False, "transcribe": True}
    assert settings["price"]["align"] == ["", ""]
    assert settings["price"]["transcribe"][0].startswith("$")
    assert "secret-two" not in json.dumps(settings)
    # and the panel is told which kinds may fill which slot
    assert "align" not in settings["offers"]["deepgram"]


def test_a_slot_that_needs_a_key_is_refused_without_one(tmp_path):
    from ytalbum.web import App

    app = App(Config(), tmp_path)
    with pytest.raises(ValueError, match="needs an API key"):
        app.save_settings({"timing_draft_provider": "deepgram"})
    with pytest.raises(ValueError, match="endpoint"):
        app.save_settings({"timing_align_provider": "http"})
