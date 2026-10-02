# Where YouTube is assumed

A read-only inventory taken on 2026-09-28 at `ff32049` (v0.7.0), before any Source boundary is
designed. Nothing here is a proposal except §5, and nothing was changed.

**The short version.** 895 lines in 49 files mention YouTube or something only YouTube has — 423 in
the code, 472 in the tests. But the volume is misleading: the *assumptions* are few and concentrated.
`youtube.py` is already a single seam that nothing but four modules import. The real coupling is
three things: **`video_id` as a track's identity**, **`Provenance.YT_MUSIC`/`YT_TITLE` as evidence
names**, and a **classifier that reads a channel**. Everything downstream of the plan — lyrics,
timing, MusicBrainz, trim, tags, the whole editor — is already source-neutral.

## 1. The assumptions, by module

### `youtube.py` — the seam that already exists (80 lines)

| symbol | assumes | a non-YouTube source needs |
|---|---|---|
| `YouTube._params` | yt-dlp options, PO token, cookies, format selection | its own client; none of this generalises |
| `.fetch(url)` | a URL resolves to a `Collection` of `Entry` | the same, from a folder scan or an API |
| `.download_audio(video_id, dest, choice)` | an 11-character id addresses audio | an opaque id the provider itself minted |
| `.probe(id)` / `.describe_combined(id)` | remote metadata is a second call | a local source knows it already |
| `.list_channel` / `.search_albums` / `.search_playlists` / `._resolve_album` | an artist has a *channel* with Releases and Playlists tabs | SoundCloud has users and sets; a folder has neither — **optional capability** |
| `.source_state(url)` | a cheap call says whether a collection changed | folder mtime; an API etag; or "always re-read" |
| `.fetch_bytes(url)` | cover art is at an http URL | a local file, or embedded art |
| `VIDEO_ID`, `_WATCH`, `_CHANNEL_URL`, `one_video`, `channel_base_url` | id and URL *shapes* | per-provider parsing, or opaque ids and no parsing at all |
| `BOT_CHECK`, `is_bot_check`, `is_transient`, `NoAudioStream`, `NotSupported` | a provider's failure vocabulary | the same five categories, provider-worded |
| `entry_from_info`, `refs_from_tab`, `ref_from_ytm_album`, `album_thumbnail`, `playlist_thumbnail`, `best_thumbnail`, `_channel` | yt-dlp's `info` dict shape | each provider maps its own payload to `Entry`/`SourceRef` |

### `models.py` — the plan's own shape (16 lines)

| symbol | assumes | needs |
|---|---|---|
| `PlanTrack.video_id` | **a track is identified by a YouTube video** | a `source_ref` opaque to everything but its provider |
| `PlanTrack.url` (property) | `https://www.youtube.com/watch?v={video_id}` | the provider builds its own link, or none |
| `PlanTrack.source_override` / `effective_id` | an alternative *video* | an alternative ref **from the same provider** |
| `PlanTrack.channel` | an uploader, used for "trim everything from this channel" | a per-provider grouping key ("uploader", "label", "folder") |
| `PlanTrack.error_kind = "no_audio_stream"` | a YouTube-specific failure reaches the UI | a provider-neutral error kind |
| `PlanTrack.audio_choice` | yt-dlp format selection (`best`/`combined`) | meaningless for a local folder |
| `AlbumPlan.source_url` / `source_id` | one URL, one id, both YouTube's | `provider` + opaque `source_id`; URL optional |
| `Entry`, `SourceRef`, `Collection` | fields named for YouTube (`channel`, `thumbnail`) | the same shapes, neutrally named |
| `Provenance.YT_MUSIC`, `YT_TITLE` | **evidence is named after YouTube** | `SOURCE_TAGS` and `SOURCE_TITLE`, or per-provider names |

### `plan.py` — classification reads a channel (35 lines)

| symbol | assumes | needs |
|---|---|---|
| `classify` / `artists_in` | one channel + title parsing decides album vs compilation vs single | the rule is really "how many distinct artists, and does the collection have an owner" — generalises if `channel` does |
| `album_artist_for` | a compilation's album artist is the **channel** | the collection's owner, whatever a provider calls one |
| `derive_artist` / `derive_title` (`Provenance.YT_TITLE`) | the title is a YouTube video title | any source with dirty titles wants this; a tagged local file does not |
| `Kind.ARTIST_PLAYLIST` | "a playlist somebody made" is a YouTube concept | keep the kind, widen the word |

### `titles.py` — YouTube's title conventions (2 lines, but the whole module)

288 lines of parsing "(Official Video)", `@handle` feat-credits, reversed `Song - Artist`, invisible
marks YouTube inserts. **Entirely a YouTube-title heuristic.** A local folder with correct tags must
be able to skip it; a SoundCloud title probably wants it. So it is a *per-provider option*, not
pipeline furniture.

### `service.py` (58) and `download.py` (19) — the pipeline

| symbol | assumes | needs |
|---|---|---|
| `Service.yt` | one provider object for the whole run | a provider resolved per album from `plan.provider` |
| `fetch`/`plan_only` → `self.yt.fetch(url)` | input is a URL | a URL **or** a path **or** a provider-qualified ref |
| `update` → `self.yt.source_state` | change detection is one remote call | a capability a provider may decline |
| `search`/`open_query` → `list_channel`, `one_video`, `channel_base_url` | artist search is channel browsing | an optional `search` capability |
| `run()` in `download.py` → `yt.download_audio`, `yt.probe`, `yt.fetch_bytes` | three calls, by id | the same three, provider-dispatched |
| `BOT_CHECK` comparisons (service:355, 417) | a specific message string identifies "blocked" | an error *type*, not a string |

### `web.py` (69) + `webui/` (80) — the UI

| symbol | assumes | needs |
|---|---|---|
| `Details` (web:208–251) | only `https://www.youtube.com/` URLs are enriched | per-provider detail fetching, or none |
| `THUMB_HOSTS` (web:78) | `ytimg/ggpht/googleusercontent` are the art hosts | a provider declares its own allowlist |
| `/api/open` | "a URL or an artist name" is the whole input space | a folder path is neither |
| `sourcePanel` (app.js:605–663) | "another **video**", 11-char ids, `watch?v=` links | "another source ref from this provider" |
| `index.html:16,44` | placeholder and button say "YouTube" | provider-aware wording |
| `open on YouTube` link | every album has a YouTube page | optional per provider |

### `config.py` (17), `pot.py` (4), `cli.py` (20), `systemd.py`, `desktop.py`

`cookies_from_browser`, `cookies_file`, `pot_mode`, `pot_port`, `pot_idle`, `pot_provider_home`,
`concurrency` ("parallel **YouTube** requests") are **all YouTube-only settings** that currently sit
in the top-level config. They want to be a per-provider settings block. `cli.py` help text says
"playlist, video or channel URL" in four places. The unit and launcher are named `ytalbum`, which is
the product, not the source — **no change needed**.

### Tests (472 lines, 26 of 32 files)

Two independent `FakeYouTube` classes (`test_incremental.py:24`, `test_search.py:19`) implement
exactly `download_audio`, `probe`, `fetch_bytes` — **the de-facto Source contract already exists in
the tests**, and it is three methods. `design-fixtures/` holds 13 recorded yt-dlp/MusicBrainz
payloads, all YouTube-shaped.

## 2. Counts

| | files | marked lines |
|---|---|---|
| `youtube.py` | 1 | 80 |
| other `src/` | 21 | 343 |
| tests | 27 | 472 |
| **total** | **49** | **895** |

Largest: `tests/test_web.py` 137, `youtube.py` 80, `web.py` 69, `webui/app.js` 69, `service.py` 58,
`plan.py` 35, `cli.py` 20, `download.py` 19, `config.py` 17, `models.py` 16.

## 3. Already neutral, and not

**Neutral** — reads the plan, never the source: `timing*.py` (0 marked lines), `lyrics.py` (1, a
comment), `mb.py` (3, two comments and `track.video_id` as a dict key), `tag.py` (3), `trim.py` (2),
`cover.py` (2), `enrich.py` (4, both comments). The lyrics editor, the near-miss check, the aligner,
the publisher and the MusicBrainz seeder would all work unchanged against a local folder.

**Not neutral**: `youtube.py`, `plan.py`'s classifier, `titles.py`, `models.py`'s field names,
`service.py`'s single `yt`, `download.py`'s three calls, the UI's input vocabulary, and the
YouTube-only config keys.

## 4. What leaks onto disk (the migration bill)

These are in files already written and cannot be renamed by code alone:

| leak | where | bill |
|---|---|---|
| `video_id` | every `.noaap.json` track | rename to a neutral ref **or** keep and read as "the YouTube provider's ref". Keeping is cheaper and honest. |
| `source_url`, `source_id` | every plan | add `provider`, default `"youtube"` when absent |
| `provenance: "yt_music"` / `"yt_title"` | every edited field in every plan | either keep the strings as historical names, or migrate on load |
| `channel` | every track | keep; widen the meaning |
| `audio_choice`, `error_kind: "no_audio_stream"` | tracks that hit it | provider-scoped |
| **`youtube_id` tag** | **written into every audio file** (`tag.py:58,87`, incl. an iTunes freeform atom) | a re-tag of the whole library to change it; leaving it is free |
| `.originals/<video_id>.opus` | every trimmed track | rename-on-migrate, or keep |

Nothing else leaks: sidecars, covers and folder names carry no source identity.

## 5. A proposed boundary

```python
class Source(Protocol):                       # one per provider, resolved from plan.provider
    name: str                                 # "youtube", "folder", "soundcloud"
    def capabilities(self) -> frozenset[str]  # "search" | "changes" | "art" | "alternatives"
    def resolve(self, text: str) -> Ref | None            # URL, path or id → a ref it owns
    def collection(self, ref: Ref) -> Collection          # one album/playlist/folder + entries
    def entry(self, ref: Ref) -> Entry                    # one item's metadata, refreshed
    def audio(self, ref: Ref, into: Path, choice: str = "") -> Path
    def art(self, url: str) -> bytes                      # "art"
    def changed(self, ref: Ref, since: str) -> bool       # "changes"; else always True
    def search(self, query: str) -> list[SourceRef]       # "search"
    def settings(self) -> dict[str, Any]                  # its own config block
```

Three notes. **`Ref` is opaque** — only the provider parses it, which removes `VIDEO_ID`, `_WATCH`
and `one_video` from the core. **Failures become types**, not the `BOT_CHECK` string comparison that
`service.py` does twice today. **`titles.py` is a provider option**, not a stage: a local folder with
good tags must be able to say "my titles are already clean".

The tests' `FakeYouTube` already implements three of these nine methods, which is the best evidence
that the shape is roughly right.
