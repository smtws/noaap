# noaap — Design

Status: draft, 2026-09-22. Replaces `ARCHITECTURE_PLAN.md` (v2) and the v1 tree in
`~/YT-Downloads-master`. Nothing from v1/v2 is carried over as code unless listed under
"Salvage" at the end.

**The program was called `ytalbum` up to and including 0.9.0** (slice 52). §9 and §12 are records of
what was decided and done on a date, so a command quoted there is spelled the way it was run; the
rest of this document describes the program as it is now. The plan file on disk is `.noaap.json`;
it was `.ytalbum.json` until 1.30.0, and `noaap migrate` renames the ones an older version wrote.

## 1. Goal

Turn a YouTube *collection* into a properly tagged album on disk:

- **Input:** a URL (playlist, channel, video) — or later an artist search.
- **Output:** `<library_root>/<AlbumArtist>/<Album>/<AlbumArtist> - <Album> - NN - [TrackArtist - ]Title.opus`,
  tagged (artist, albumartist, album, title, tracknumber/total, disc if >1, date,
  embedded cover), without re-encoding YouTube's Opus stream.
- **Re-runnable:** running it again on the same source downloads only what is new
  (e.g. a growing playlist like My Dark Lullabies Vol. 20).

MusicBrainz is an **enricher, never a gatekeeper.** A collection with no MusicBrainz
entry is downloaded with the best metadata YouTube offers, which the user can edit before
downloading.

## 2. The three cases

| Kind | Example | Album-level data | Track-level data |
|---|---|---|---|
| `official_album` | YT Music album playlist (`OLAK5uy_…`), Topic channel release | MB release if matched, else YT Music fields | MB tracklist if matched, else per-video `track`/`artist` |
| `artist_playlist` | an artist's own playlist, a YouTube-only band | playlist title / channel | per-video music fields, else cleaned video title |
| `compilation` | My Dark Lullabies Vol. 1 (14 bands, 14 uploaders) | albumartist = curator, album = playlist title minus curator prefix (see §2.1) | per track: YT music fields → title parsing → MB recording lookup |
| (`chapter_album`) | one "full album" video with chapters | video title / channel | chapters |

### 2.1 Compilation naming (decided 2026-09-22)

- **albumartist = the curator** (the playlist's channel, e.g. `My Dark Lullabies`).
- **album = the playlist title with the curator prefix removed**:
  `My Dark Lullabies Vol. 1 - Heavy Sleeping` → `Vol. 1 - Heavy Sleeping`.
- The curator's own titles are inconsistent (`Vol.1`/`Vol. 1`, `-`/`–`), so the album
  title is normalised to `Vol. N - Title` with an ASCII hyphen, so all 20 volumes look
  and sort alike. Editable in the preview like any other field.
- Result: `Library/My Dark Lullabies/Vol. 1 - Heavy Sleeping/My Dark Lullabies - Vol. 1 - Heavy Sleeping - 01 - Enemy Inside - Lullaby.opus`
  (the `[TrackArtist - ]` part is always present for compilations).

### 2.2 Classification

Classification (deterministic, testable, overridable in the preview):
1. Playlist id starts with `OLAK5uy_` → `official_album`.
2. Single video with chapters → `chapter_album`; without chapters → single track.
3. Playlist whose entries resolve (after step 4.2) to one artist → `artist_playlist`,
   several artists → `compilation`.

## 3. Verified facts about yt-dlp (checked 2026-09-22, yt-dlp 2026.08.19)

These are the facts v1/v2 got wrong or never knew. Fixtures in `design-fixtures/`.

1. **`playlist_count` on a flat entry is the size of the list the entry was found in,
   not its own track count.** `ytsearch5:` → every video has 5; `@channel/playlists` →
   every playlist has 20. This single misread caused the whole v2 fix/break loop
   ("MB:11 / YT:46"). A playlist's real size is only known after fetching *that* playlist.
2. **Full (non-flat) extraction of a single video returns music metadata**: `artist`,
   `artists`, `track`, `album`, `release_year` — not only for Topic channels but also for
   band channels (Schandmaul "Prinzessin" → album "Anderswelt", 2008). Flat entries don't
   have these. This is the primary source for compilation tracks.
3. **Artist-channel "Full Album" playlists are not albums.** Sabaton "Legends (Full Album)"
   = 11 songs + feat. versions + the full-album video + 3 documentaries (17 entries; the
   old debug scripts expected 7). Playlists change over time → tests must use saved
   snapshots, never live data.
4. **Compilations contain non-songs**: Vol. 1 starts with a 3 s intro card uploaded by the
   curator. Rule: skip entries < 30 s. (NOT "title equals the playlist title": that also
   matches Sabaton's 45-min "Legends (Full Album)" video inside the Legends playlist.)
5. **yt-dlp now needs a JS runtime** ("No supported JavaScript runtime… some formats may be
   missing"). Only deno is enabled by default; node is installed here and needs
   `js_runtimes` config. Must be solved in setup, or formats (incl. Opus 251) may vanish.
6. Opus 251 (~148 kbps) exists for normal videos; download it and remux, never transcode.
7. Channel `/releases` tab exists only for official artist channels; a curator channel
   (MyDarkLullabies) has only `/playlists`. "Artist - Topic" channels have neither tab
   any more; YouTube Music's album search (`music.youtube.com/search?q=…#albums`) returns
   `MPREb_` ids that resolve to the `OLAK5uy_` playlists, whose tracks name the real
   uploading channel (Faun → "fauntube").
8. **A playlist may list the same video twice.** Mono Inc's "The Clock Ticks On" album
   playlist lists seven acoustic versions a second time. Both entries became tracks with
   the same wanted filename, overwrote each other and left the renamed older copies as
   orphans. A video is one track per album, in `usable_entries` and in `merge_plans`.
9. **YouTube's bot check.** After a few hundred requests in a day, every video answers
   "Sign in to confirm you're not a bot" while playlist listings still work. A run then
   sees a playlist whose entries all failed — and v3 briefly reclassified Vol. 1 from that
   partial view and moved it to `Erben der Schöpfung/…`. Rules since: failures are
   *transient* (bot check, 403/429/5xx, timeouts) or permanent (private, deleted,
   age-restricted); a skipped video is still *in* the source; any transient failure means
   "incomplete — change nothing"; the first bot check stops all further requests
   (fetch, download, `update`, multi-picks). Defaults: 2 parallel requests, 0.5 s apart.
   **A logged-in browser session helps:** with Firefox cookies
   (`cookies_from_browser = "firefox"`) the same day's full `update` ran without a single
   bot check. (Chrome cookies stopped working for the owner earlier — Chrome changed its
   cookie storage.)
10. **Age-restricted videos** become *readable* with a login, but an account that is not
   age-verified gets only format 18 (360p video, low-bitrate AAC); all audio-only streams
   are withheld or need a PO token. It is *not* the account: the same video plays in high
   resolution in that very Firefox, because the browser presents a proof-of-origin token.
   No yt-dlp client (tv, web_safari, mweb, web_embedded) gets audio without one.
   **Fix (2026-09-22):** the bgutil PO-token generator in script mode —
   `bgutil-ytdlp-pot-provider` (plugin, in the venv) + its generator built in
   `.pot-provider/server` (v2.0.0, Node, gitignored). noaap detects it and passes
   `youtubepot-bgutilscript:server_home`. Result: Opus 251 offered, Feuerschwanz track
   downloaded at 121 kbps. Script mode costs a Node process per request (a full 4-album
   update took 2.5 min), so the default is now **server mode** (`pot.py`): before reading
   or downloading, noaap pings `127.0.0.1:4416/ping` and, if nothing answers, starts a
   detached watchdog (`python -m noaap.pot`) running `node build/main.js` on localhost
   only; every YouTube request and download progress touches
   `~/.cache/noaap/pot-server.heartbeat`, and after `pot_idle` (300 s) without a beat the
   watchdog stops Node and exits. Script mode stays configured as the plugin's fallback.
   `pot_mode = "script" | "off"` in the config switches. v3 never transcodes the 360p
   fallback into Opus.

## 4. Pipeline

Each stage is a plain function: input → output, no shared mutable state, no network in
stages 3–5. Stages 1–2 do all YouTube I/O.

```
URL ─► 1 resolve ─► 2 inspect ─► 3 classify ─► 4 enrich ─► 5 plan ─► [preview/edit] ─► 6 download ─► 7 tag+place
```

1. **resolve(url)** → `Source` list. Channel URL → its playlists (and releases tab if
   any) as separate sources. Playlist → one source. Video → one source.
2. **inspect(source)** → `Collection` with real entries: fetch the playlist itself,
   then full-extract each entry (bounded concurrency, e.g. 4) to get duration, chapters,
   music fields, availability. Unavailable/private entries are recorded as `skipped` with
   a reason — never abort the collection.
3. **classify(collection)** → kind (§2). Pure.
4. **enrich(collection)** → proposed metadata, every field carrying its **provenance**
   (`mb`, `yt_music`, `yt_title`, `playlist`, `user`):
   - album level: MB release search only for `official_album`/`artist_playlist`;
     accept only if artist AND title match and track count is within ±1 of songs.
   - track level: YT music fields → title parser (§5) → optional MB recording lookup.
5. **plan(collection, metadata)** → `AlbumPlan`: folder, filenames, tags, cover source,
   list of video ids to download. Written as JSON next to the album
   (`.noaap.json`) — this is also the manifest for incremental re-runs.
6. **download(plan)** → Opus files, one at a time or small concurrency; each track is
   tagged and moved into place as soon as it finishes (interrupt-safe, v1's good idea).
7. **tag(file, track)** — mutagen `OggOpus`, cover as `METADATA_BLOCK_PICTURE`.

The user can stop after step 5 (`--dry-run`), edit the plan, and run step 6–7 from it.

## 5. Title parsing (compilation / YouTube-only)

Order of trust for a track's artist + title:
1. yt-dlp `artist`/`track` fields (§3.2).
2. Channel is `<X> - Topic` → artist = X, title = video title.
3. Channel name ≈ first part of `A - B` title → artist = A.
4. Parse `A - B` / `A "B"` / `A – B`; strip noise: `(Official Video)`, `(Official Music Video)`,
   `(Official Lyric Video)`, `(Official Visualizer)`, `[4K UPGRADE]`, `(LYRICS)`,
   `| <Label>` suffix, `(feat. @handle)` → `feat. Handle`.
   A dash only separates artist and title when what follows it is one: if the right side is
   *only* a video label (`"Mad World" (feat. Gary Jules) - Official Music Video`), the whole
   text is the song and the artist comes from the channel (added 2026-09-23; v2 handled this
   shape with ~40 literal suffix strings, which is exactly the tuning this rule replaces).
4b. **A playlist on the artist's own channel is not a compilation of that channel** (2026-09-23).
   Its video titles carry the album around — "Feuerschwanz Methämmer - Song by Song - …",
   "Das Elfte Gebot - Unboxing" — which counted as two more artists, so `classify` said
   compilation and the *channel handle* became the album artist (`xxFEUERSCHWANZxx`).
   `named_artist()` is what classification and the album artist now count: the artist a title
   names, with the guest credit removed (§5.6) and a trailing copy of the playlist title
   stripped; an entry that names none (only the channel is left) counts for nothing.
   Fixtures `artist_channel_playlist{,2}.json`.
5. Label / lyrics / fan channels (Napalm Records, "Common Sense", "dernachtwaechter")
   are never the artist. Reverse order ("Lullaby of Woe - Ashley Serena") is only fixable
   by a lookup (MB recording search both ways) or the user.
5b. **When the uploader stands in as the artist, the title usually still names the real one**
   (2026-09-23, found by auditing the library for `artist == channel`). Rules, each general and
   each from a real case: a credit at the end ("No Sound But The Wind **by The Editors**") is
   read only when nothing else names an artist — a song may simply contain the word ("Killed by
   Death"); a title that repeats our own artist loses it ("Metallica: Nothing Else Matters");
   `'single quotes'` delimit a title like double ones ("NEBELUNG 'Mittwinter'"); a colon
   separates; a dash separates without a leading space only when what follows starts a name
   ("Arcana- Innocent Child" splits, the German compound "sang- und klanglos" does not); and
   invisible bidi/zero-width marks are stripped before anything else ("In The Nursery ‎– …",
   whose U+200E hid the separator).
5c. **Video facts are not audio facts** (2026-09-23). "(1080p)", "HD 1080p", "Full HD" and
   the label words in other languages ("Oficial") describe the upload and are dropped —
   "(Remaster)", "(2012 Remaster)", "(Remastered 2023)" describe the recording and stay, so
   "(Remastered 1080p)" keeps its audio half. A trailing run of noise words is judged as a
   whole and only dropped if one of them labels a video: "Full HD" goes, "Life Is Full" stays.
5d. **A publisher suffix after a slash goes** (2026-09-23): "U-Gra (Tagelharpa playthrough) /
   Napalm Records". A slash is not a separator like "|" — real titles contain one ("Intro /
   Outro", "AC/DC", "24/7") — so the segment must name a publisher (Records, Recordings,
   Entertainment, Productions, …) before it is cut. Checked against the library:
   "Hexentanz / Henkersmahlzeit / Gebt Acht!" and "Auschwitz / Birkenau" stay whole.
5e. **An album name repeated in every track is a label** (2026-09-24). The shop writes it
   wherever it likes: "1 - Der Kuss des Kometen (Teil 01)", "Kapitel 01: Die Hexenmeister des
   Metal (Folge 4)", "Louder Than Hell (Live in Hamburg)". It is removed wherever it sits,
   together with a bracket group that only repeats the release and the empty pair left behind.
   Judged per album, never per title: at least three tracks and 80% of them must carry it, so
   "Carolus Rex (Swedish version)" among fifteen unrelated titles keeps its name — stripping
   that one would have left "Swedish version". A single-word album never strips, and nothing
   is cut when the remainder would be empty.
   Three bugs this found, each with a test: `casefold()` maps "ß" to "ss", so a pattern built
   from the album silently missed "Auf großer Tour"; a bare "4" from "Folge 4" matched inside
   "Kapitel 04" without word boundaries; and MusicBrainz enrichment re-adds bracket groups it
   does not know, so the judgement is made again after enrichment, not only in `build_plan`.
6. **Guest credits live in the title, never in the artist field** (2026-09-23):
   `Feuerschwanz ft. Melissa Bonny` / `Ding` → `Feuerschwanz` / `Ding ft. Melissa Bonny`,
   applied to all three sources (video title, YouTube Music, MusicBrainz artist-credit).
   Otherwise every collaboration becomes its own "artist" with its own folder. The marker is
   kept as written, a guest already named in the title is not repeated, and a credit naming
   the track's own artist is dropped (`Gary Jules` / `Mad World (feat. Gary Jules)`).

Every rule gets a fixture case from `design-fixtures/vol1.json`; the expected results are
the right-hand column of the table in §8.

## 6. Data model (one shape, versioned)

```
Collection  { source_url, source_id, kind, title, channel, fetched_at, entries[] }
Entry       { video_id, position, title, channel, duration, chapters[], music{artist,track,album,year}, status: ok|skipped(reason) }
Field<T>    { value: T, provenance: mb|yt_music|yt_title|playlist|user }
AlbumPlan   { schema: 1, album, albumartist, year, cover, folder, tracks[] }
PlanTrack   { video_id, number, disc, artist, title, filename, state: pending|done|failed(reason) }
```

- "Entries in the playlist" and "songs on the album" are different numbers and are
  never stored in the same field.
- Counters shown in any UI are **computed** from these lists, never maintained by hand.
- Schema changes bump `schema` and come with a migration of `.noaap.json`; there is
  never more than one live shape.

## 7. Tech choices

- Python 3.14, `uv`-managed venv, `pyproject.toml` with pinned deps
  (`yt-dlp`, `mutagen`, `httpx`; nothing else until needed).
- yt-dlp **Python API** (`YoutubeDL.extract_info`) in a thread pool with a semaphore —
  not dozens of CLI subprocesses. Timeouts on everything.
- No cookies by default. Optional exported `cookies.txt`; never `--cookies-from-browser`
  on every call (v2 decrypted Chrome's DB ~50× per search).
- MusicBrainz: real UA with contact, one async-safe limiter (≤1 req/s, lock + monotonic
  clock), retry on 503, Lucene escaping; disk cache (sqlite) for hits; misses cached
  short (1 h), errors not cached.
- Lyrics: **LRCLIB** (`lrclib.net`, no key, community-contributed), same client shape as
  MusicBrainz — UA with the repo URL, ≤1 req/s, retry on 503 (it answers "server is busy"
  readily), sqlite cache (hits 30 d, misses 7 d). Measured 2026-09-25 on 40 random library
  tracks: 50 % synced, 22 % plain, 28 % nothing. The text is third-party and unlicensed;
  noaap only puts it beside a file the user already has, and `--no-lyrics` / `config
  --lyrics off` switches it off.
- ffmpeg only for remux (`-c:a copy`) and chapter splitting.
- Config: one TOML file (`~/.config/noaap/config.toml`), overridable per run by CLI
  flags. Holds `library_root` (**configurable, no default path baked into code**; the
  first run asks, or takes `--library`), filename template, compilation naming rules,
  MB on/off, concurrency, JS runtime.
- UI: **CLI first** (`noaap fetch <url> [--dry-run] [--edit]`). Web/PWA later on top
  of the same library — the library never knows about the UI.

## 8. Test cases (all offline from saved fixtures, plus one opt-in live smoke test)

| Fixture | Asserts |
|---|---|
| `tab_playlists.json` (@MyDarkLullabies/playlists) | channel → 20 sources; flat `playlist_count` is never used as a track count |
| `vol1.json` (Vol. 1 - Heavy Sleeping) | kind = compilation; intro card skipped → 13 tracks; parsed artists: Enemy Inside, Dominum (the `feat.` belongs in the title, §5.6), Mono Inc(.), Schandmaul, Subway to Sally, Lacrimosa, Letzte Instanz, Mantus, Erben der Schöpfung, Disturbed, Ashley Serena*, Lord of the Lost, Tungsten (*needs lookup) |
| `legends.json` (Sabaton channel "Full Album" playlist) | kind = artist_playlist, not official_album; MB release "Legends" must NOT be auto-accepted (17 entries vs 11 songs) |
| to capture: an `OLAK5uy_` album | kind = official_album; MB match accepted; tracklist from MB |
| to capture: a chaptered full-album video | chapter split, titles from chapters |
| to capture: Carolus Rex | release-country preference picks the international edition |
| to capture: albums titled "1984" / "1918" | normalisation never strips the year-like title |

## 9. Vertical slices (each ends with something usable)

*Slices are the numbered items below; "§9, slice 44" means item 44 in this list.*

1. ✅ **Paste playlist URL → tagged album folder** (any kind, metadata from YouTube only),
   plus `--dry-run` plan output. Includes JS-runtime setup (§3.5). *Done 2026-09-22:*
   Vol. 1 downloads 13/13 (Opus 251 copied, 147 kbps), tags + 1280×720 cover embedded,
   resume and per-track retry (YouTube sporadically answers 403) verified live.
   Known gaps, left for their slices: raw video titles for 8 of 13 Vol. 1 tracks
   (slice 2), YouTube covers are 16:9 not square (fixed later: `cover.py` crops
   pillarboxed thumbnails to the square art — only when everything cut away is uniform
   background, centred unless content forces a shift, never user covers), a single
   video is filed as a 1-track "album" under its YT Music album name.
2. ✅ Compilation parsing (§5) + intro skipping; Vol. 1 comes out right. *Done 2026-09-22:*
   11 of 13 Vol. 1 tracks exact; the two left (reversed "Song - Artist" on a lyrics
   channel, `@xxHANDLExx` feat. credit) are by design for the lookup in slice 5.
   Also: `release_year` exists on plain videos too (upload year) — only trusted together
   with an `album` field.
3. ✅ Incremental re-run from `.noaap.json` (Vol. 20 grows → only new tracks). *Done
   2026-09-22:* the plan records the auto-derived value of every field; a differing
   value is a user edit and always wins, untouched fields follow better derivations.
   `folder`/`filename` in the plan mean *what is on disk*; wanted names are computed and
   the executor renames/moves (never overwriting). Tags carry a signature → retag only
   on change (e.g. tracktotal grows). `cover.*` in the album folder is used (user can
   replace it). Albums are found by source id, so renamed folders are still updated.
   Verified live: re-running Vol. 1 renamed 8 files to their slice-2 names, retagged
   13, downloaded 0. **Corrected later:** "existing numbers stay, new tracks are appended"
   was wrong for curated playlists — Feuerschwanz (12th in Vol. 20, readable only later)
   became track 13. Numbers now always follow the source order (MusicBrainz' numbering
   for matched releases); tracks gone from the source go last. Re-sorting the playlist on
   YouTube → the next fetch renames and retags, downloads nothing.
4. ✅ Channel URL → pick which collections to fetch. *Done 2026-09-22:* Releases tab
   (official `OLAK5uy_` albums + singles) listed before Playlists; `--pick`/`--all` or
   an interactive prompt; "✓ in library" markers; `ytalbum update` re-checks every album.
   Found on the way (Vol. 20): age-restricted videos → opt-in cookies
   (`config --cookies-from-browser/--cookies-file`), skipped until then and picked up by
   a later `update`; YouTube titles can be Unicode-decomposed (NFD) → normalised to NFC
   at the mapping boundary; German/360°/unbracketed video labels; partial labels keep
   their meaning ("(Official Live Video)" → "(Live)"), and brackets are only treated as
   labels if they contain a marker word ("(Music of the Night)" stays).
5. ✅ MusicBrainz enrichment (album + recording level) with provenance and cover art.
   *Done 2026-09-22:* Vol. 1 13/13 recordings (incl. the reversed "Lullaby of Woe" via a
   swapped query; the credit "DOMINUM feat. Feuerschwanz" picked because the
   `@xxFEUERSCHWANZxx` handle contains the guest's name — since 2026-09-23 stored as
   artist "DOMINUM" + title "The Dead Don't Die feat. Feuerschwanz", §5.6), Vol. 20 9/12, official Legends matched as a release
   (year, tracklist, 500×500 Cover Art Archive front, `musicbrainz_*id` tags). The fan
   "Full Album" playlist is correctly *not* accepted as the release. Rules found on the
   way: a title with extra info MB lacks ("(Live)", "(Behind The Scenes Documentary)")
   confirms the artist but gets no recording id; MB answers 503 even to the first
   request, so retry/backoff is mandatory; our own saved cover is upgraded when a better
   source appears, a user's cover never is. Cache: `~/.cache/ytalbum/musicbrainz.sqlite3`.
5b. **Third lookup strategy: split the title** (2026-09-23). When the uploader stood in as the
   artist (`artist == channel`) and the normal and swapped queries found nothing, the real
   artist is often glued to the title with no punctuation ("Assemblage 23 Lullaby",
   "Joachim Witt Gloria"). Every split is tried and `pick_recording` must confirm *both*
   halves, so a query for the right words cannot return a wrong band. Unbracketed trailing
   video labels are dropped first ("Gloria Offizielles Musikvideo"), else the title half
   never matches.
6. ✅ Artist search (channel releases/playlists tab, Topic channel, YT playlist search).
   *Done 2026-09-22:* `ytalbum search "Faun"` → YouTube Music album search → dominant
   uploading channel whose name contains the artist → its Releases tab; grouped as Albums
   (MusicBrainz studio albums or ≥5 tracks) / Singles, EPs / channel playlists / other
   playlists; duplicates of one album merged; MB albums not found are listed. Curators
   (no YT Music albums) are found through their own playlists. Verified live: Faun →
   13 albums, HEX downloaded with MB release match (guest credits) in one command.
   Also fixed here: a new album's folder follows the enriched names; a merge never
   replaces a value by a less trusted one (user > MB > YT Music > title/playlist), so an
   `update --no-mb` or an MB outage cannot undo MusicBrainz corrections.
7. ✅ Web UI / PWA on the library. *Done 2026-09-22:* `ytalbum serve` — stdlib HTTP
   server + plain HTML/JS (no build step, no WebSocket state machine); library grid with
   covers, album view with editable fields and provenance badges (edits = USER values,
   then rename/retag on disk), one input for URL-or-artist (preview / pick from search
   or channel), "Update library", job cards with logs. Jobs run one at a time in a
   single worker. Orchestration moved to `service.py`, shared by CLI and web. Safety:
   localhost by default, writes need an `X-Ytalbum` header (no cross-site POSTs), Host
   check (DNS rebinding), covers only by album id, strict CSP, all YouTube text rendered
   as text. Installable as an app when opened via localhost (service workers need a
   secure context; over plain http on the LAN it works as a web page only).
   Verified in a browser: grid, album view, update job ending "blocked" cleanly.
   Later: theme switch (auto/dark/light, remembered per browser); the page references
   `app.js`/`style.css` by content hash — a one-hour cache had kept the old script (no
   browser dropdown) alive after an update.
8. ✅ Intro/outro trimming — **manual, after automatic detection was measured and dropped**
   (2026-09-22). Measured on the owner's library: 0 of 39 videos have chapters; two
   Napalm Records uploads share no detectable opening (loudness correlation +0.46, while
   two unrelated Sabaton tracks reach +0.69) and no common silence structure; MusicBrainz
   lengths show *that* there is extra material (30 of 66 tracks longer, up to +234 s) but
   never where, and the big ones are cinematic music-video intros where a cut would hit
   the song. So: trim points per track in the plan (`trim_start`/`trim_end`), cut
   losslessly with `ffmpeg -c copy` from `.originals/<video_id>.opus`, which is kept, so
   clearing the trim restores the original byte for byte; one button applies a trim to
   every track of one uploader across the library.

9. ✅ Library hygiene and desktop integration (2026-09-23). Artist fields hold the performer
   only — yt-dlp lists writers and producers in `artists` too, MusicBrainz credit phrases
   carry guests, and both had produced folders like `Feuerschwanz feat. Melissa Bonny`.
   Added: performer-only extraction, guest credits into the title (§5.6), one spelling per
   artist across the library, and a video listed twice in a playlist counted as one track.
   `ytalbum repair` applies all of it offline to what is already on disk (21 tracks in 12
   albums here), and skips anything the user edited.
   Also `ytalbum app install`: a browser-installed PWA keeps the browser's window class
   (`WM_CLASS = "crx_<app-id>", "Google-chrome"`), and desktops group the taskbar by that
   class, so it shows up as another browser window. No manifest key changes it — the class
   comes from the browser process, and `--class` is only honoured by a process of its own,
   so the launcher pairs it with a profile directory of its own.

10. ✅ Finding things in the library (2026-09-23). The grid sorts artist → year → name, so a
   discography reads chronologically while compilations, which have no year, keep their
   natural order. A filter over album, artist **and song** titles: `/api/tracks` serves every
   track as compact rows (video id, artist, title, downloaded, trim points) with a version
   taken from the plan files' mtimes, so the page fetches it once and again only when an
   album changes — 95 KB and 11 ms for 1338 tracks, and typing costs no request. Matching
   folds case, accents and punctuation, plus what NFD cannot: ð/þ/ø/æ never decompose
   ("njord" → *Njǫrð*), and a German keyboard writes "knueppel" for *Knüppel*. Folding records
   where each folded character came from, so the match is highlighted in the original text.
   An input's value cannot be highlighted character by character (no CSS, no Custom Highlight
   API, and mirroring text behind a transparent input breaks on scroll, fonts and IME), so a
   matching field in the album editor is tinted whole. **▶ Play** fills the existing queue
   from the filter — the matching songs of each album, or all of an album that matched by
   name. Deliberately not built: shuffle, repeat, reordering, persistence. The player is here
   to check downloads, not to replace a music player.

11. ✅ "No audio-only stream" is not a property of the video (2026-09-23, from real use).
   DOMINUM "One of Us" failed with it and a plain re-fetch got the full-quality Opus
   (`ext=opus`, `audio_choice=best`); Skeeter Davis' 1963 upload fails every time and really
   offers only *360p video, AAC*. Identical symptom, opposite cause — and the old dialog
   pushed the user toward an `.m4a` that is audibly worse than a file that was available.
   So the download asks a second time (after making sure the token server is up) before
   raising `NoAudioStream`, and the dialog now says the failure can be temporary and to
   re-check the source first. Only a video that refuses twice is treated as having no audio.

12. ✅ Two things real use turned up on 2026-09-24.
   **An album of unusable videos is not an album.** Heavysaurus' eight *Folge* Hörspiele are
   YouTube Music Premium exclusives: all ~31 entries per album answer "only available to
   Music Premium members" (confirmed with the owner's own cookies). That reason is permanent,
   so the §3.8 guard — which holds a run back when entries fail *temporarily* — let them
   through one by one, and a plan with zero tracks was written: eight folders under "Unknown
   Artist", cover art and nothing else. A source whose every video is unusable now reports and
   writes nothing.
   **A disc split survives an update.** `merge_plans` took `disc` from the fresh plan, and a
   YouTube playlist is always flat, so splitting an album into media by hand would be undone
   by the next update. A flat source now says nothing about media; only a fresh plan that has
   discs of its own (a matched release) may change them, and a video that appears later joins
   the last disc.

13. ✅ One spelling per artist, chosen by evidence (2026-09-24). The library had "SALTATIO
   MORTIS" beside "Saltatio Mortis" and three spellings of Lord of the Lost, for three
   reasons, each fixed: the album artist was unified *after* `relocate` had chosen the folder,
   so a renamed album stayed put; `_harmonize_artist` renamed without refreshing the derived
   paths, so a new album kept the folder of the spelling it had just dropped; and `repair`
   compared names only, skipping albums whose plan was already right but whose folder was not.
   The spelling itself is now chosen by evidence rather than by alphabet: one the user typed
   wins, then one MusicBrainz confirmed, then mixed case over a shouting channel name. Before
   that, "Lord Of The Lost" beat "Lord of the Lost" because "O" sorts before "o".

14. ✅ The track order can belong to the user (2026-09-24). A YouTube playlist's sequence is
   often just the order things were added in — the owner of My Dark Lullabies keeps the
   canonical order on Spotify, and resorting 15 volumes by hand would have been undone by the
   next update, because `merge_plans` renumbers from the source. Editing a position now sets
   `provenance["order"] = user` on the album; a merge then keeps the user's numbers and puts a
   video that appeared since at the end. The position is an input in the album view, and the
   play button moved into a cell of its own so showing it on hover no longer shifts the row.

15. ✅ Lyrics belong to the file, not to the tagger (2026-09-25). Three measurements decided
   the shape. LRCLIB's exact endpoint (`/api/get`) needs *its* album name and the length
   within ±2 s, so for compilations only the `/api/search` path can work — and a search on
   artist + title alone is what attaches a cover version's words to the original, so a
   candidate is refused unless its length is within 3 s of the **file's own** (mutagen, after
   trimming; YouTube's duration includes intros). `tag_file` replaces every tag on every pass,
   so the `LYRICS` comment (`©lyr` on m4a) is written *from* a `.lrc` sidecar rather than
   preserved: the sidecar is the truth, delete it and the tag goes too. That also serves the
   player that matters here — MPD/Volumio has no lyrics tag at all and reads `.lrc`. The text
   is not kept in `.noaap.json` (3946 × ~3 KB would land in every `update` and in the track
   index, which is 11 ms today); the plan holds only `lyrics: synced|plain|instrumental|none`
   and the lrclib id, so nothing is ever looked up twice. Two things about lrclib's
   `instrumental` flag, learned from real use: it means *nobody submitted words*, not that the
   recording has none (16 of 18 entries for one Feuerschwanz song are such stubs), so an entry
   without words never ends the search; and when our own title says "(instrumental)" the sung
   version's words are refused however well the lengths agree, because an instrumental cut is
   exactly as long as the sung one and only the title can tell them apart. The marker is read
   from the **track** title alone: an album called "(Instrumental)" claims something about
   every track on it, and such records do turn up with a sung intro or outro, while a track
   title is written per recording. It costs nothing here either — every instrumental track in
   the library carries the word itself, so the album name would reach none of them. A trim changes the file's length, so the
   track is matched again afterwards — but its timestamps are never moved: the match was
   gated on *this* file's length, so the recording that matched is the audio in front of us.
   (Shifting them by the trim, as the first version did, moved them a second time.) Two things the
   first run over the real library taught: lrclib's exact endpoint refuses a duration over
   3600 s (a 63-minute ambient piece got HTTP 400 on every run), so that request is not made
   at all, and any 4xx is now remembered as "nothing here" — only 5xx and network errors are
   worth asking again. Coverage over 3946 tracks: 52 % synced, 15 % plain, 4 % instrumental,
   29 % nothing; audited against lrclib on 200 matches, none had the wrong artist or song.

16. ✅ A credit's typography is not the artist's name (2026-09-25). "Visions of Atlantis"
   stood in the library twice, and both spellings honestly carried `mb`: MusicBrainz credits
   releases as they are printed, and three of seven credit "Visions **Of** Atlantis" while the
   artist entity is always lower-case. With nothing left to weigh, harmonisation fell through
   to its last resort, the alphabet, where "Of" beats "of". A credit now yields the artist
   entity's own spelling whenever the two differ only in case or punctuation — `key(credit) ==
   key(entity)`, the same equivalence class every other inference step works in, so a credit
   that names something *else* ("Puff Daddy" for the artist "Diddy") is a deliberate editorial
   decision and is kept. It costs no requests: `inc=artist-credits` already carries
   `artist-credit[].artist.name` beside the credited name, at release **and** track level.
   Measured over the 175 releases in the cache: 168 credits identical, 5 restyled, 0 named
   differently — and MusicBrainz is not quietly de-stylising bands, since its canonical name
   for DOMINUM is "DOMINUM". Nothing in the library changes on a later update (the five were
   already repaired by hand), and the two albums where the user chose "UNIVERSUM25" over
   MusicBrainz' "Universum25" keep that choice, as `user` outranks everything.

17. ✅ The player hears the file the trim describes (2026-09-25). Clicking a lyric line to
   jump there only works if the clock in the page is the clock in the audio, and it was not:
   trim points count from the start of the *video*, but `/api/audio` served the file already
   cut to them, so the player cut the head a second time. Measured on the one trimmed track
   in the library: playback started 8.16 s into an already-trimmed file, and the first eight
   seconds of the song could not be reached at all. A track whose file was cut is now played
   from `.originals/` (`/api/audio?o=1`, the file `trim.apply` keeps anyway) and the player
   previews the trim itself, which puts the trim handles, the text fields, the lyric
   timestamps and the audio on one clock again. The line being sung is then marked as the
   song plays — the box scrolls itself, never the page, because `scrollIntoView` would take
   the editor with it (slice 12 learned that the hard way).

18. ✅ Three opinions on how long a song is (2026-09-25). MusicBrainz knows the recording,
   lrclib answers even when its recording was too far off to take the words from, and the file
   on disk is measured when it is tagged (`file_length`) — so a disagreement is visible without
   opening anything. The thresholds come from the library, not from taste: 13 % of 3032
   comparable tracks are more than 5 s longer than MusicBrainz, so marking *any* mismatch would
   mark 138 of 245 albums. The track shows the signed gap (amber past 20 s, red below 60 % of
   the known length — that is not the song), and an album is flagged only when **half** its
   comparable tracks are off by more than 20 s the same way: 11 albums of 245, a worklist
   rather than wallpaper. The first one it caught was "Sabaton — Heroes", eleven
   track-commentary clips of 30–50 s filed as the album, because YouTube's own title
   ("Heroes (Track Commentary Version)") lost its bracket group to `core()` when the release
   was matched and MusicBrainz then named it "Heroes". Replaced by the real album, where
   every track now lands within 3 s. Two thresholds were corrected once the library answered:
   a *stub* (a file under 60 % of the known length) flags an album on its own, because only 8
   of 3946 tracks are one and every one was a snippet, a radio edit or a wrong recording;
   while a whole-album drift needs **three** tracks, not two — at two, a volume where only
   four tracks can be compared at all was flagged for a pair of long folk songs. A rule that ignored
   lengths repeating across an album (23 of Judas' 56 tracks read 222.1 s) was **built and
   then reverted the same day**: the audio release turned out to have 24 tracks of exactly
   223 s, because those collaborations are one arrangement sung by different guests. The
   repetition was real data, and suppressing it hid a true flag — that album's files were
   official videos running ~50 s long. Repetition is not evidence of a bad reference.

19. ✅ A bracket group can say the recordings are different ones (2026-09-25). `core()` strips
   bracket groups before comparing titles, which is right for "(Deluxe Edition)" and wrong for
   "(Instrumental)": the first names the same recordings, the second does not. Measured on 35
   library albums against their real YouTube titles: 5 had an edition marker MusicBrainz
   lacked (correctly dropped), 1 a version marker — "OPVS NOIR Vol. 1 (Instrumental)", filed
   as the ordinary album. A release candidate is now refused unless its version markers match
   ours; re-fetched, that album did not merely keep its name, it matched the *right*
   MusicBrainz release. The same measurement found the companion fault: a track whose
   recording was refused ("(Live)", a cover) still kept that recording's length, so 55 tracks
   carried a length they never had and 21 of them were marked by the new chip — a live cut
   against its studio version reads as two minutes off. `mb_length` is now only written when
   the recording is accepted, and `repair` gives up the ones already stored.

20. ✅ A trim keeps the track's own format, and a failure says so (2026-09-26, from the QA
   run). The kept original was named `<video id>.opus` whatever the track was, and the cut was
   always written into `.trim.opus` with `-c copy`. A track trimmed as opus and then switched
   to the combined stream was therefore re-cut from the *previous format's* original: ffmpeg
   copied Opus into a file named `.m4a`, the tagger called `MP4()` on it and raised, and that
   exception aborted every later run over the album until the file was deleted by hand — while
   the plan still read `state=done, trimmed=None, error=None`. Now the original carries the
   track's extension, the cut keeps the track's container, and a kept original that is not what
   its name says is never cut from: if the file on disk is still the untouched download a fresh
   original is taken from it, otherwise the trim is refused with the recovery path named. No
   original is ever invented from an already-cut file. Two further faults came out of the same
   case: a trim that fails **was never recorded** — `run()` set `track.error` and then saved
   nothing, so with ffmpeg missing the plan advertised a trim that never happened and the web UI
   showed nothing at all — and a file that cannot be tagged now fails **its own track** instead
   of the album's run. Prune also gives up the kept original, as `delete_track` always did.
   Two mirrors of the same rule came out of the review: **clearing** a trim with nothing to
   restore from is refused as well, because a plan that calls a cut file untouched is the same
   lie in the other direction; and a re-downloaded track gives up its `trimmed` signature,
   since a fresh file is untouched whatever the plan said before — otherwise the trim points
   sit there matching a signature that describes a file that no longer exists, and are never
   applied again.

21. ✅ Lyrics beside a track belong to whoever wrote them (2026-09-26, from the QA run).
   "Lyrics you write yourself are never touched" held only if you had also set the flag that
   said so: editing a `.lrc` by hand and re-running `ytalbum lyrics --refetch` overwrote it, and
   deleting one left the plan claiming `synced` while no file was there. Both because the plan
   knew the *status* of a lyric but nothing about the file. It now records the bytes it wrote
   (`lyrics_sha`, the twin of the cover's `sha1`), and every pass reconciles the plan with the
   disk before anything is looked up: a sidecar whose hash has changed is yours from then on,
   a sidecar that is gone sets the status to `none` and drops the tag, and a sidecar with no
   lrclib id behind it was never ours to begin with. The interesting case is the library that
   predates the record. The obvious test — does the file still say what the tag says? — sounds
   decisive and is not: every pass writes the tag **from** the sidecar, so an edit made before
   the last pass reads back as perfect agreement, and that is the *common* case, not the rare
   one. So a difference to the tag is taken as proof of an edit, agreement proves nothing, and
   the question is left open until something actually wants to overwrite the file — at which
   point lrclib is asked what the entry we saved holds today. Equal means ours (the hash is
   recorded and it is never asked again), different means yours, and no answer at all means the
   file is kept and the question stays open. The check costs nothing for a track looked up
   recently, because the cached search bodies already carry whole rows. Two smaller holes went
   with it: `--refetch` used to clear the `lyrics_id` it needs to ask that question, and
   `ytalbum lyrics` skipped albums with nothing to look up — so on that path a deleted sidecar
   was never noticed. A trim, finally, clears the status to force a re-match; for a lyric of
   yours it now restores the status from the words on disk instead of leaving the track blank.

22. ✅ The arrangement is the order the tracks are in, not the numbers (2026-09-26, from the QA
   run). Two faults with one cause. Prune closed the numbering gap on a single-disc album and
   left it on a multi-disc one — `1, 2, 4 …` in the tags — because the decision was made by
   `if all(t.disc == 1)`, not by any rule about order. And collapsing a disc split back to one
   disc reshuffled the album: the numbers of a split are per disc, so `1-01…1-03 / 2-01…2-03`
   sorted by `(disc, number)` interleaved into 1, 1, 2, 2, 3, 3. A split was reversible on paper
   but not in arrangement. The fix names what the arrangement actually is: the order the tracks
   stand in, which is the order the album view shows and the order the browser posts back.
   `renumber_discs` is therefore `arrange`, and it no longer sorts at all — it groups by disc and
   counts each disc from 1. Where a sort *is* wanted, because the user typed numbers, it happens
   first and against the disc each track **was** on, where the numbers are unique; a number just
   typed also beats the same number left standing on another track, so typing 1 on 2-04 makes it
   lead and moves 2-01 down. Prune then closes the gap on every album, per disc, and keeps
   `provenance["order"] = user`: that flag protects the sequence from the *source* renumbering it,
   and a deletion the user asked for is not the source — `delete_track` has always renumbered.
   The sort had to go entirely in the end, because a number the user types is a *position* and
   sorting cannot deliver one: a track moved down still sorts ahead of whatever holds the place
   below its target, so typing 5 on the first of five tracks put it fourth, and no typed number
   could move a track to the end at all — sorting can place a track before the one whose number
   it typed, never after it. The upward move worked, which is why the asymmetry stayed hidden.
   So the typed tracks are lifted out of the arrangement and put back at the index they asked
   for, lowest number first, while the untouched ones keep their relative order (`placed`). Two
   typed positions in one save, one up and one down, both land where they were asked to.

23. ✅ A fetch renames only the album it is fetching (2026-09-26, from the QA run). Unifying the
   spelling of an artist looked done — `SCHANDMAUL` and `Schandmaul` are one folder — but only
   when the newcomer was the worse-spelled one. Arriving with a *better* spelling, it simply kept
   it: the library's other albums stayed as they were and the artist had two folders (E7). The
   obvious fix is the wrong one. A fetch of one single must not rename twenty other albums as a
   side effect; that is `ytalbum repair`'s job, run deliberately. So the incoming album adopts the
   spelling the library already holds, even when its own evidence is better, and when it *is* the
   better evidence one line names both spellings and says `repair` unifies them. A spelling the
   user chose for the album being fetched still wins for that album — the one case that leaves a
   second folder, and it is theirs to make. The trade-off, accepted: an older spelling can stand
   until repair runs.
   Two pieces make that promise keepable. First, an album is made consistent with *itself* before
   the library is consulted: MusicBrainz credits the release and the tracks in separate fields and
   they disagree (`LORD OF THE LOST` on the release, `Lord of the Lost` on every track), so when
   the most common track artist is key-equal to the album artist, spelled differently, and carries
   MB provenance, the album adopts the tracks' spelling. Second, `repair` does the same step — and
   it has to, or the hint would be a lie: the library adoption overwrites the album-level spelling,
   so after the fetch the better evidence exists **only** in the track credits, where repair's
   album-level comparison would never have seen it. Measured read-only over the 246-album library
   before shipping: 0 albums would be renamed by that step today, and the 3 whose tracks disagree
   with their album artist are all albums the user spelled themselves, which the first guard
   protects. A dry run runs the whole harmonisation too, so the preview is the outcome — it used
   to print the pre-harmonisation spelling and differ from what the fetch then wrote.
   Two details cost a verification round each. An adopted spelling must carry the evidence *it*
   has, not the evidence the album had: adopting while the album's own name came from MusicBrainz
   left the `mb` marker on a shouted name, and repair then converged on the shouting, confidently.
   And repair's older rule ("use the most common track artist when the album artist came from
   YouTube") renames without leaving a marker, so the tracks' spelling lost a tie to another
   mixed-case spelling in the library — alphabetically, which is a coin flip. Both fixed by naming
   the evidence at the moment the spelling is taken; `user` is never inherited either, since it
   would freeze the album against later harmonisation.
   Repair also had to stop asking the question once per album. Deciding against the library *as
   stored* meant an album already visited could not learn from evidence found later, so three
   albums in three spellings took two passes to settle — "run repair" could mean "run it twice".
   One scan now collects every candidate for an artist key (each album-level spelling with its
   provenance, plus `track_spelling`'s answer where its guards hold), the key is **decided once**,
   and every album of that key that is not the user's adopts it in the same pass. Same fixed point,
   one scan instead of one per album, and the pass after it has nothing to do. A tie between two
   equally common track spellings is settled by evidence and then by `spelling_rank`, because
   `max(set(names), key=names.count)` settled it by set iteration order — which hash randomisation
   makes differ between runs. On the real 246-album library repair changes nothing at all, verified
   by copying every plan into a scratch tree and running the real `repair` over it.

24. ✅ What lrclib says a song is long is a consensus, not a nearest miss (2026-09-26, from the
   QA run). When no candidate fits our file, the length kept as the second opinion used to be the
   candidate nearest to *our* length — which for a padded upload is the least representative one
   there is: for the 471 s *Viva Vendetta* video, lrclib's nine entries read 229, 229.8, 229.8,
   230, 230, 230, 230, 230 and 248, and the nearest was the 248. It is now the commonest whole
   second (230), the median of the tied values when nothing repeats more than anything else. The
   question is about the song, so every same-artist candidate answers it together.
   The second half is the query. An instrumental cut has no entry of its own, so asking lrclib for
   "Viva Vendetta (Instrumental)" returned **0 rows** — measured — and the track ended up with no
   length reference at all, although the sung recording's length is exactly the reference it wants.
   The query now drops the NO_VOCALS markers (instrumental, karaoke, backing track) and nothing
   else: a "(Live)" or any other bracket group goes to lrclib as it stands, because a live cut
   really is another recording and studio words must never attach to it. Refusing the *words* for
   an instrumental is unchanged — the marker is still read from the track title, and only wordless
   entries may match — so such a track now gets a length and still gets no lyrics.
   What moves, measured read-only over the real library (stored plans plus the cached lrclib
   bodies, no requests): 16 tracks' lrclib reference changes, most by a second or two and four by
   20-50 s; **no album flag changes** (12 flagged before, 12 after, the same 7 "long" and 5
   "stub"); and 4 per-track chips move, all between nothing and the muted 5 s band. 11 tracks in
   2 albums cannot be computed from the cache, because their searches were made with the marker and
   returned nothing — those are the ones that *gain* a reference, and only a lyrics pass will say
   what it is.
   Accepted, not overlooked: the marker is matched as a bare word too, so a song whose real title
   contains "instrumental" is asked for under a shorter name. The same set already decided whether
   a track's *words* are refused, so the two behaviours stay identical rather than drifting apart —
   which is the property worth keeping here.

25. ✅ A single is one song, so its album name is that song's name (2026-09-26, the user's decision:
   "consistency is more important here than file storage operations", so folder renames are
   accepted). `build_plan` reads a single's album name off the *video* title and enrichment then
   improves the **track** only, so the two drifted apart in the one place a user sees both: S9's
   album read `The Dead Don't Die (feat. @xxFEUERSCHWANZxx)`, the uploader's handle and all, while
   its one track read MusicBrainz' `The Dead Don't Die feat. Feuerschwanz` — and the folder was
   named after the album. After enrichment (and in `repair`, for singles already on disk) the album
   name is set to the track's title, folder and file names following through the existing relocate
   path. Guards: only `kind == SINGLE` with exactly one track, and never an album name the user
   chose. A track title the user chose *is* followed, but the album's provenance is then left as it
   was rather than set to `user` — marking it would freeze the album, so a later edit of the same
   title would stop reaching it. Measured read-only before shipping: the library holds exactly one
   single, whose name is the user's own and already equal to its track title, so `repair` renames
   nothing; the rule is for what arrives next.

26. ✅ The lyrics panel writes as well as reads (2026-09-26, backlog item 1). The whole ownership
   contract of §9, slice 21 is about words a user writes by hand, and the only door to it was the file
   system: find the audio file, name a sidecar with the same stem, write LRC syntax, run a pass.
   The ♪ button now opens a panel that edits, and it appears for a track with no words at all
   (faint) and for one LRCLIB calls instrumental, since those are exactly the tracks whose words
   somebody would want to write. Saving does in one step what `reconcile` does when it *finds* an
   edited file: the sidecar is written as entered, the user mark set, the hash recorded, the status
   derived from the text (`synced` when a line carries a timestamp, else `plain`), the LYRICS tag
   rewritten from the file, the plan saved. **Nothing is looked up** — an editor that asked LRCLIB
   could answer a save by replacing the words just typed. An empty save is a *clear*, not an empty
   file, and it drops the mark with the words, so a later `--refetch` may bring LRCLIB's version
   back exactly as deleting the file by hand does.
   The retag goes through the ordinary pass (`run(..., download=False)`, no lyrics client) rather
   than a second tagging path, so there is one place that writes tags. The price is that a Save is
   not strictly local to one track: the pass walks the album, so a trim left pending on another
   track (one whose `ffmpeg` was missing when it was set, §9, slice 20) is applied then — the same thing
   any other pass would have done, reached from a new direction. The write itself is a job in
   the write lane like every other library change, and jobs now carry the album they hold
   (`Job.target`), so a save is **refused** while a pass is working on that album instead of racing
   it — a pass would retag from the very file the save is about to write. A `fetch` is named by its
   URL and only learns the album id while it runs, so it is not one of the jobs that check can see;
   that is a known gap, not a silent one. A track that is not `done` has no file to put words
   beside and is refused too.
   Nothing about the contract needed a special case for the editor: a sidecar it wrote, then edited
   again on disk, is still the user's, and deleted on disk it follows §9, slice 21 like any other.

27. ✅ One track can be looked up again, and a wrong entry can be rejected for good (2026-09-26,
   backlog item 3). The lyrics button was all-or-nothing: shift-click re-asked LRCLIB for a whole
   album, and the only way to refuse a bad match was to delete the file on disk — which the next
   `--refetch` undid by matching the same wrong entry again. The panel now offers, for words that
   are not the user's, **Look up again** (this track only, with its title, artist and file length as
   they are now) and **Not these words**.
   Rejecting is not "delete": the entry's id goes onto `PlanTrack.lyrics_rejected`, and `Lrclib.get`
   drops rejected ids before anything is judged. So no later lookup can pick it — not a per-track
   one, not a pass, not a `--refetch`. That is the difference that makes the action worth having:
   an entry being the wrong recording stays true however often it is asked for, while a deleted file
   only says "not now". Rejecting immediately takes the **next** best candidate under the unchanged
   rules, or leaves the track at `none` when nothing else fits, so one click ends in an answer
   rather than in an empty panel.
   Rejected ids are dropped from the length consensus too (§9, slice 24), not only from the words: an entry
   that is not this song is no evidence about how long this song is either.
   Neither action is offered for lyrics marked as the user's — those are not LRCLIB's to replace, and
   the editor's Delete is the way to let it answer again (§9, slice 26). Both are write jobs with the album
   in `Job.target`, so they are refused while a pass holds it, and a track with no file is refused.

28. ✅ The fetch preview is the outcome (2026-09-26, the other half of backlog item 2). A preview of
   a URL already existed — `/api/open` has always run `fetch(dry=True)` on the read lane and shown
   the plan — so this slice is about the three ways it was not yet the truth, and about not making
   it compulsory.
   First, **an album already in the library was previewed as if it were new.** The dry branch
   returned before the merge, so the plan shown was a fresh reading of YouTube: it promised names
   that a real fetch would not write, because the fetch merges with the stored plan and keeps every
   value the user has edited. The dry path now merges too (and logs "already in the library as …,
   N new, M no longer there"), which costs nothing — nothing is saved — and makes the preview
   answer the only question worth asking. The panel says which state it is in, and the button reads
   "Download what is missing" rather than "Download".
   Second, the preview showed no sign of tracks that have left the source; those rows are now marked
   *gone*, as the album view marks them.
   Third, **the preview must not become a compulsory click**: Shift+click on Go (or Shift+Enter)
   posts the fetch directly, the same modifier convention as everywhere else in the UI. A form
   submit carries no modifier state, so it is captured on the way in, on the form's capture phase.
   One thing the page itself caught during verification: the in-library line printed the server's
   absolute path, which is no business of a browser. It shows the library-relative folder, and only
   mentions a move when the album would change folders.

29. ✅ A value you overrode can be handed back (2026-09-26, backlog item 5). The plan has always
   kept what the pipeline derived, in `auto`, for every field a user overrides — that is how a merge
   knows which values are theirs — but the UI offered no way back. An edited album artist was frozen
   out of harmonisation and repair with nothing to click, and the only route was editing the JSON.
   The badge that says "you" **is** the way back now: where a field is the user's and something was
   derived for it, the badge is a button that restores the derived value. What it drops matters less
   than what it writes: `_merge_fields` decides a field is the user's by comparing the value with
   `auto` and re-asserts the USER provenance on every merge, so dropping the mark alone would be
   undone by the next update. The provenance is dropped rather than guessed at, because `auto`
   records the derived *value* and never its source; the next pass that touches the field writes a
   truthful marker again. Where nothing was derived (an album from before `auto` was kept) the badge
   stays a badge and says why.
   The order flag resets too, and only that: nothing is renumbered at the moment of the reset, but
   the next update may put the album back in the source's order. The tooltip says so, because a
   button that silently rearranges 56 tracks would be a trap.
   Lyrics are deliberately not in this: the editor's Delete is their way back (§9, slice 26), and two
   affordances for one thing would only be two things to explain.
   Measured read-only over the library this is for: 59 of 246 albums carry at least one overridden
   field — 38 album artists, 20 years, 15 user orders, 3 album names — and 83 tracks (73 artists,
   17 titles). Every one of them has an `auto` value behind it, so every one is resettable.

30. ✅ Opening an album asks the disk (2026-09-26, backlog item 6). Ownership of an edited `.lrc`
   and the status of a deleted one were only noticed when some pass walked the album, so between
   passes a row could show ♪ for words that were no longer there — safe, because `reconcile` runs
   before anything overwrites a file (§9, slice 21), but a user would call it a bug. `/api/album` now runs
   the same `reconcile` for every done track before it answers, so the view is the truth as soon as
   it is drawn; when it found something, one write job saves the plan and brings the tags along,
   with the album in `Job.target` so it queues behind anything already working on it. When plan and
   files agree — the normal case — nothing is written and no job exists, which is the property worth
   testing: three opens of an unchanged album leave every file's mtime untouched.
   The grid is deliberately *not* reconciled: 246 albums would be stat-ed on every render of a page
   that polls. Its counts come from the plans, so they catch up when an album is opened, which is
   the moment a user is looking at that album anyway.
   Cost, measured on the 56-track album (30 of them with lyrics): one `stat` per done track, plus
   reading the sidecar where there is one and the audio's tag where a sidecar has no recorded hash.
   `/api/album` answers in **4–5 ms** against 2–3 ms for `/api/state`, so the check costs about 2 ms
   for 56 tracks; the whole open, measured in the browser, is 22 ms. It is bounded by the album, not
   by the library, which is why the grid is left out of it.

31. ✅ The ⏱ mark leads to a cut (2026-09-26, backlog item 7). The chip said a track was the wrong
   length and left the user with arithmetic: the trim fields are bare seconds, and the only way to
   know whether a cut fixed the length was to save it and look at the chip again. Marking from
   playback already existed (*start here*, *end here*, draggable handles, arrow keys for tenths), so
   this slice is the two things that were missing.
   **A target while trimming.** The trim bar now says what the pending marks would leave, what the
   song is said to be, and the difference — "now 4:45 · keeping 3:38 · MusicBrainz 3:37.6 · +0.4s" —
   recoloured on the chip's own bands as the marks move, so the user watches the gap close instead of
   guessing. The arithmetic lives in `plan.trimmed_gap` and is mirrored in `app.js`'s `trimTarget`;
   the reference is the chip's own (`reference_length`), so there is no second opinion to keep in
   step. It counts from the **video's** duration, never from a file already cut, because that is what
   trim points mean (§9, slice 17) — the test for that case is the one that would catch a future refactor.
   **Which file you are hearing.** A cut track is played from its kept original, or the head would be
   skipped twice (§9, slice 17); the player now says so, and *▶ from start* plays from the start mark, which
   is the question a start mark actually raises ("does the song begin here?").
   Marks are rounded to a tenth. `audio.currentTime` carries a dozen decimals of mouse precision that
   mean nothing musically and end up in the plan and on ffmpeg's command line.
   Deliberately not here: automatic cut detection and a waveform. §9, slice 8 measured detection and dropped
   it, and a chip that leads to a cut does not need to guess the cut.
   The JS half has no unit tests, because this repo has no JavaScript test harness and P14 is not the
   place to introduce one; `plan.trimmed_gap` carries the arithmetic under test, and the browser
   checks in the catalog (section P) are the evidence for the rest.

32. ✅ Rows are dragged, and a typed number counts in the disc you put the track on (2026-09-26,
   backlog item 4, the last of them). Typed positions have landed correctly since §9, slice 22, but typing
   numbers into 56 rows is a poor way to reorder an album. A row now has a grip (`⋮⋮`) in the position
   cell and is dragged with **pointer** events rather than HTML5 drag-and-drop, which does not exist
   on touch; the row moves through the table as the pointer passes other rows, so what is on screen
   is the arrangement that will be saved, and the position column is renumbered per disc on every
   move so it never shows two 3s mid-edit. Escape puts every row back. Alt+↑ / Alt+↓ on a focused row
   does the same move without a mouse. Nothing is saved until the album's save, exactly as a typed
   position is not.
   The grip is deliberate: making the whole row draggable would fight the text selection in the title
   and artist fields, which are the other thing a user does in that table.
   **What this changed on the server, and it is the interesting half.** The client posts every row
   with its number and disc, as the browser already did, so there is no second ordering path — but a
   cross-disc drop did not land where it was dropped. `placed()` grouped every track under the disc
   it *came from*, so a row dragged from disc 2 into the middle of disc 1 ended up after disc 1's
   rows rather than between them (measured: dropped at 1-02, landed at 1-03). A number the user
   **typed** now counts in the disc the track is being put on; a number left alone still counts where
   the track was, which is what keeps §9, slice 22's collapse from interleaving the two discs. One line, one
   function, and the drag lands.
   One case only the browser could show: a row dragged into another disc often keeps its *per-disc*
   number by coincidence — 2-02 dropped at 1-02 is still "2" — so a changed number cannot always
   say that the user moved it, and without that knowledge it was read as a row that stayed put and
   filed after its new disc's rows. The payload now carries `moved` for rows the user has actually
   put somewhere since the last save, which is the UI stating an intent instead of the server
   inferring one. A collapse sends no `moved`, so §9, slice 22's case is untouched.
   That also changed a case reviewed in P3: typing "1" while collapsing two discs into one used to put
   the track first of its *former* disc-2 block (seventh), and now puts it first of the album. Under
   one disc, "1" means first; the old reading was defensible only while numbers were read under the
   old discs for every purpose. The test carries the reasoning.

33. ✅ The page's own logic has tests (2026-09-26, backlog item 10, the user's call after two UI
   defects shipped green). `app.js` had grown to ~1,500 lines carrying real rules — the length
   target, the arrangement and its live renumbering, what a panel offers, the filter's folding — and
   none of it ran under a test. What it *did* have was the Python twins and the Playwright cases,
   which is why the two defects that shipped (§9, slice 29's invisible badge, §9, slice 31's scattered buttons)
   were caught by looking at pictures; neither a unit test nor a DOM assertion would have found them,
   and that is the honest limit of what this slice buys.
   The split is `webui/logic.mjs` — everything that computes rather than draws, exported — and
   `app.js`, which imports it. The page loads it as `<script type="module">`, which browsers do
   natively, so **there is still no build step**: the reason the module keeps the `.mjs` extension is
   that node then treats it as ESM without a `package.json`, and the repo stays free of npm. Tests
   are `node --test` with `node:assert`; `tests/test_js.py` shells out to them so `uv run pytest`
   runs everything, and skips with a reason where node is missing (CI installs it rather than
   skipping quietly).
   The twins are what earn it: `tests/shared/trim_target.json` is one table of twelve cases that
   `plan.trimmed_gap` and `logic.mjs`'s `trimTarget` are both tested against, so a rule changed on one
   side fails on the other. §9, slice 24's rounding, §9, slice 22's per-disc numbering, §9, slice 32's drop semantics,
   §9, slice 21's panel rules and §9, slice 29's badge decision are pinned the same way.
   Caching needed one more thing than the split: a module's import URL is inside the module, where
   the index's rewriting never reached, so a new `logic.mjs` could have sat behind a cached `app.js`
   that never asked for it. `IMPORTS` in web.py versions the import when the module is served, and
   folds the imported file into the importer's own hash.
   Left browser-only, deliberately: everything that needs a DOM — the drag's pointer handling, the
   panel's rendering, the player. Testing those would mean jsdom, which is the npm dependency this
   slice exists to avoid; the catalog's sections H and K–R remain their evidence.

34. ✅ A track may take its audio from another video (2026-09-27, P22). The user's case: the
   playlist holds the *official video* of "Und 'n Tripper" — theatrical material at both ends, ten
   seconds of creaking floor in the middle, +0:23 against MusicBrainz — while the canonical 3:04
   recording exists on YouTube as its own upload. Until now the only answers were to trim around the
   film (which cannot remove the middle) or to let the album hold the wrong recording.
   **The playlist video stays the track's identity.** `source_override` is a second field, never a
   replacement for `video_id`: the order, `in_source`, prune, the merge and the MusicBrainz match all
   keep looking at the playlist's video, and `PlanTrack.effective_id` is what the *audio* side asks —
   the download, the kept original, the uploader. Ownership rides on the existing provenance system
   (`provenance.source = user`, `auto.source` = the playlist id), so §9, slice 29's badge is the way back
   with no new mechanism behind it.
   **A source change is a re-download, and it says so first.** Everything the old file carried is
   about a different recording: the state, the tags, the trim marks, the measured length, and the
   uploader and duration, which follow the audio (a channel-wide trim must not cut this file to
   another channel's ident). The kept original of the previous source is deleted — it could only ever
   shadow the new one — and originals are keyed by the effective id, so going back re-downloads
   cleanly. The UI confirms with the marks named in it ("The trim 1:30–5:20 belongs to the current
   file and will be cleared"), because a mark silently kept would cut the wrong seconds.
   **The words are never touched, and the timings say what they were written for.** §9, slice 21 stands: the
   user's lyrics are theirs through any switch. But timestamps written against the old file point at
   the wrong seconds of the new one, and nothing in the file can say so — hence `lyrics_for_source`
   and `lyrics_for_length`, stamped by every sidecar write (ours or the editor's), and a notice in the
   panel until the words are saved again. It names the two lengths only when they really differ: the
   same song from another upload measures 3:50 against 3:49 by rounding alone, and a pair of numbers
   that look identical says nothing. LRCLIB-owned lyrics are simply looked up again, with the new
   length, which is usually the canonical one and matches where the film cut never could.
   **No automatic re-timing here.** Shifting a user's stamps is a change to their work and needs its
   own design and its own confirmation; it is P23's (`docs/backlog.md` item 13).
   One rule the parser carries: a *single video* is a bare id or a watch/share/shorts/embed link in
   any of YouTube's shapes, and a playlist, a channel or a search is refused. A `watch?v=…&list=…`
   link is one video — that is what YouTube hands you from inside a playlist — and the list part is
   ignored. The page refuses a bad paste before it sends it and the server refuses it again;
   `tests/shared/video_ids.json` is the one table both are tested against (§9, slice 33).

35. ✅ Stamping the words to the file's own clock (2026-09-27, P23, backlog 13). The user timed
   "Und 'n Tripper" by hand and every stamp landed up to three seconds late. Three causes, measured
   in that order: the track is trimmed from 1.6 s and **a trimmed track is played from its untouched
   original** (§9, slice 15 — the marks count from the start of the video, so the player must hear the file
   those numbers describe), which puts the player's display on the video's clock while a lyric stamp
   belongs to the *cut* file's; the display shows whole seconds; and there is the latency of hearing,
   deciding and pausing. `lyricsLines` already added the trim back when *seeking* — but a human
   reading the display and typing it does not subtract anything. Nobody should read one clock and
   type the other.
   **What the editor does now.** One key (Ctrl/⌘+Enter, and a button for touch) writes the moment
   being heard onto the line the cursor is in, converted to the file's clock and rounded to a tenth,
   and moves to the next line; a line that already had a stamp is rewritten rather than given a
   second one. Alt+Enter plays from the current line's stamp. Alt+← / Alt+→ move it by a tenth, with
   Shift by half a second, and play it back from there, so alignment is done by ear rather than by
   arithmetic. A readout beside the tools shows the position **in the file** with tenths, and both
   clocks side by side whenever they differ — which is exactly the case that caused this.
   **The shift** (backlog 13's original ask) moves every stamped line by a typed number of seconds,
   leaves unstamped lines where they are, and is reversible by shifting back.
   **The textarea stays the only source of truth.** Every one of these rewrites its text and nothing
   reaches the disk until Save, so the ownership contract (§9, slice 21, §9, slice 26) is untouched and a stamp
   written by the tap is byte-for-byte what a hand-typed `[mm:ss.t]` would be — verified by reading
   the saved `.lrc` back.
   **The offset is the trim the file was *cut* to, not a mark being placed.** `trimOffset` reads the
   saved mark, because the `.lrc` belongs to the file on disk; the two differ exactly while someone
   is dragging a trim handle. The same helper now serves the seek and the sung-line highlight, which
   both used the pending mark before and were a fraction out in that one state.
   Deliberately unchanged: the player's own display and the trim bar keep the original's clock
   (outcome 6 of the task, and §9, slice 15's reason — the trim marks depend on it).

36. ✅ Words on a clock, behind a provider boundary (2026-09-27, P25, backlog item 14). P24 measured
   what a local model can do and the answer was worth having: wav2vec2 CTC alignment on a separated
   vocal stem places the lines of 17 of 20 real tracks within a median of 0.94 s, with no penalty for
   growled vocals or for German, from 442 MB of models and no GPU. The question P25 answers is not
   "can it" but **where the inference lives**, because the one thing ytalbum must not acquire is a
   baseline of "modern GPU plus gigabytes of CUDA and weights": most machines do not have it, the
   user's older desktop may never, and the core product — fetch, name, tag, trim, LRCLIB — has to
   stay exactly as usable without any of it.
   **Two capabilities, not one.** `transcribe` derives words from audio; `align` places words you
   already have. They have different markets (every speech vendor sells the first; of the mainstream
   ones only ElevenLabs sells the second) and different costs (3 GB against 442 MB). ytalbum wants
   the second far more often, so the interface keeps them apart and a provider answers
   `capabilities()` for itself.
   **`none` is the default and is not a special case.** It answers `frozenset()`, the page renders no
   action, and nothing imports a model: `timing_local` is imported by `provider()` and by nothing
   else. An installation that never touches the config is byte-for-byte what it was.
   **Three providers.** `local` is the optional extra `ytalbum[timing]` (torch, torchaudio, demucs;
   ~1.5 GB with the CPU build of torch) and needs no GPU — measured at 12.2 s per track with one and
   108.4 s without. `http` is `ytalbum timing-serve` on another machine, which is the deployment this
   house wants: the app holds a URL and inherits no dependency at all, and the audio stays on the
   network. A commercial provider is backlog 15 and waits on a decision that is the user's, not the
   code's: whether their audio may leave the house.
   **The result is a proposal, not a write.** The align action fills the editor's textarea; the user
   plays a line, nudges it, shifts it (§9, slice 35) and presses Save, which is `save_lyrics` exactly as
   before. So the ownership contract (§9, slice 21) needed no new rule: the words stay the user's, the
   *clock* is recorded separately as `lyrics_timed_by`, and the panel says "timed by local" beside
   "yours" rather than claiming both. A line the provider will not place keeps its words and gets no
   stamp — `unplaced` is in the type, because every method measured in P24 fails on some track and a
   guess would be indistinguishable from an answer.
   **The alignment job is read-lane.** It writes nothing, so it may run beside a download, and — the
   part that had to be fixed — a read-lane job must not trigger the album panel's rebuild, or the
   proposal is thrown away the instant it arrives (catalog W7).
   Left out on purpose: transcription (P24 measured it as the weaker half and no provider here
   offers it), a library-wide pass, and the second aligner for an automatic cross-check, which is
   backlog 16. One aligner plus the user's ear is the safety valve, and P23's ▶ is what makes the
   ear cheap.

37. ✅ Paid providers, and a draft that says it is one (2026-09-27, P26, backlog item 15). The user
   decided the question §9, slice 36 left open — *the audio comes from YouTube anyway, so it may leave the
   house* — with two conditions that shaped the package: **no money is spent building it, and no key
   is needed to finish it.** So it was written against the vendors' documentation and verified
   against a server of my own speaking their shapes; not one request in this repository has been
   billed, and the live test per vendor is skipped unless somebody puts a key in the environment.
   **Two vendors, because two capabilities.** ElevenLabs sells forced alignment of supplied text —
   the only mainstream vendor that does, and the capability ytalbum wants most — and transcription
   beside it. Deepgram sells transcription and `capabilities()` says exactly that: it never claims
   `align`, so the page never offers one. A provider that overstates itself is worse than one that
   cannot do the job.
   **A draft is not lyrics.** Where a track has no words at all, a transcribing provider can propose
   some; the editor takes them labelled *"a machine's guess, half a song for some tracks"*, because
   that is what P24 measured. It is never offered where words exist — LRCLIB's entry or the user's
   own are both better than a guess — and the server refuses it there too. `lyrics_words_by` records
   whose words they were, beside §9, slice 36's `lyrics_timed_by` for whose clock, and the panel shows
   "words by elevenlabs" next to "yours" rather than letting one claim swallow the other.
   **The audio leaves the machine, and it is said three times**: in the README's provider section, in
   the settings row beside the choice (with the vendor's list price and the date it was read), and in
   a confirm before the first request of a session — *"The audio of this track is sent to X… ytalbum
   never retries, so one press is one request."*
   **Money shapes the error handling.** There are no retries: a retry on a metered endpoint is a
   second invoice for the same answer. A refusal, a rate limit and an unreachable host all come back
   as `TimingUnavailable` carrying the vendor's own message, and the job log names the minutes of
   audio a request is about to send *before* it sends them.
   **What is sent is the file on disk** — the cut one — because that is the file the stamps belong
   to (§9, slice 35). `YTALBUM_TIMING_BASE_<VENDOR>` redirects a client at another host: a gateway, a proxy,
   or the fake that made this package testable without a bill.

38. ✅ A second opinion, and words from this machine (2026-09-27, P27, backlog item 16). A second
   optional extra, `ytalbum[timing-check]` (faster-whisper, stable-ts; **3.09 GB of model on first
   use**), which buys two things the first extra cannot: an alignment checked against an independent
   method, and a draft of the words without a paid vendor. It is a *second* extra precisely because
   of that size — the architecture exists to keep it out of the baseline, and `timing` alone stays
   ~1.5 GB with the CPU build of torch.
   **Two methods, and only what they agree on.** The primary pass is unchanged (wav2vec2 CTC over a
   separated vocal stem); the check is a Whisper decoder hearing **the mixed track**, and the mix is
   not a shortcut but the measured requirement — on the stem the same model left 11 of 42 lines
   unplaced and put the rest 20 s early, on the mix it landed within 0.7 s of a hand-checked
   sidecar. Hearing something different is also what makes it a second opinion rather than a second
   pass. `verified()` keeps the *first* method's number where the two are within
   `timing_verify_threshold` (2.0 s) and drops the stamp where they are not. **Which regime applies is
   decided by a second, larger constant** — `timing_verify_lost`, 5.0 s. At most half the comparable
   lines that far apart means the two agree about the *track*, and the per-line rule runs. More than
   half means one of them has lost the song, and then **every primary stamp is kept** and the notice
   says how total the disagreement was: *"a second method disagreed about the whole track — 14 of 20
   lines more than 5 seconds apart — so this is one method's word: play the first line before you save
   it."*
   **That inverts the rule this slice was built with, and the reason is a measurement.** The first
   version placed nothing at all in the whole-track case. Catalog Y asked what that cost: the
   condition fired on five of sixteen real tracks, and on all five the CTC pass was the accurate one —
   twice to within a tenth of a second of a hand-checked sidecar — while the Whisper pass was 28 to
   120 s out. Placing nothing caught a bad primary nought times out of five and discarded a good
   alignment five times. A rule that cannot tell *which* method is lost must not throw away the one
   the evidence favours; telling them apart is backlog 21. The per-line rule is switched off in that
   regime for the same reason — it would strip most of the stamps anyway and make the inversion
   hollow. Both constants come from the same distances, which fall into two shapes with nothing
   between them: eleven tracks at a median under 1.5 s, five at 28–120 s.
   **The check may never cost the alignment.** Any failure in the second pass — a missing library, a
   full graphics card, a model that merges lines — leaves the primary result standing with
   `unchecked` naming the reason. This was found by running it, not by reasoning: three separate
   failures did take the whole job with them first (catalog Y).
   **8 GB of VRAM does not hold three models.** The aligner, the separator and 3 GB of Whisper
   together overflow this laptop's card, so the cache is emptied before the check, an out-of-memory
   is retried on the processor with a message that says why, and — the one that actually mattered —
   Whisper is *released after each track*, because keeping it resident is what killed track two.
   ctranslate2's CUDA-12 `libcublas` trap is caught at the same place, since it fires on the first
   inference and not on the load, where the guard originally sat.
   **Transcription is local now too**, and labelled exactly as P26's drafts are: a guess, offered
   only where a track has no words, `lyrics_words_by = "local/large-v3"` on save. `timing-serve`
   advertises whatever the machine it runs on actually has, so the `http` provider inherits the
   second opinion when the serving machine has the extra and nothing when it does not — which is the
   whole point of asking a provider what it can do instead of assuming.
   **What it costs, measured on sixteen tracks both ways:** about **+60%** on either device — 11 s → 18 s
   a track with the laptop's GPU, 2:46 → 4:29 without one. Which tracks cost most is not predictable
   from whether the methods agree (the four divergent ones added *less*, +41% against +61%), so there
   is nothing to optimise by; what drives it is how much of the track Whisper re-decodes.
   Default: `timing_verify` unset means *check whenever the extra is installed*, because someone who
   paid 3 GB for a second opinion wants it.

39. ✅ The words being edited are what plays (2026-09-27, P29, from the user's own test). *Alignment
   works, but a proposal cannot be verified without saving it* — the user's finding, in their words:
   **two views fed by two truths.** The follow-along highlight and the click-to-seek list read the
   saved sidecar through `/api/lyrics`, while the proposal lives in the textarea, so the only way to
   judge a whole song was to save it and find out. Alt+Enter on one line is not a judgement of a song.
   **While the editor is open, the textarea is the truth for playback.** A read-only list sits below
   the textarea, drawn from the textarea's own words, and it is the thing the player marks: the line
   being sung is the last stamped line at or before the current moment, converted with the file's
   clock exactly as a stamp is (§9, slice 35). Clicking a line seeks to it. Nothing about the saved file
   changes — the list writes nothing, Cancel puts the file back in charge, and Save does what it
   always did.
   **Every rewrite is seen, because they all go through one door.** The tools in the editor set
   `textarea.value` directly, and an assignment fires no `input` event, so the list would have
   followed typing and ignored the stamp, the nudge, the shift and the provider's proposal — the
   four things it exists for. `editorText()` is that door; the redraw is debounced by 150 ms so
   typing does not rebuild a 66-line list on every keystroke.
   **The list is named**, because an unlabelled second copy of the words reads as the saved ones
   shown twice: *"what you are editing, as it will play — click a line to hear it"*. That was a
   screenshot's finding, not an assertion's.
   The pure half is two functions in `logic.mjs` — `timedLines()` reads the textarea, `nowLine()`
   picks the line for a moment — and they are what the seven node tests exercise, including the case
   the feature is for: the same moment, before and after an edit, marking different lines.
   Left out: marking the current line inside the textarea itself. A textarea has no per-line styling,
   so it would mean replacing it with a contenteditable, and the list beside it already answers the
   question.

40. ✅ Two slots, because the two jobs are bought in different places (2026-09-27, P28, backlog 17).
   `timing_provider` was one setting for two capabilities, and the two are not bought together: the
   machine that aligns best is `local` — free, needs 1.5 GB of models — while the one that writes
   down words nobody has is a vendor, metered, needing nothing installed. Choosing Deepgram for
   drafts therefore gave up local alignment, and choosing `local` gave up drafting unless the 3 GB
   second extra was installed.
   **`timing_align_provider` and `timing_draft_provider`,** each falling back to `timing_provider`
   when empty — so every config file written before today still says what it said, and one setting
   still means both. `kind_for(cfg, capability)` is the only place that decides, and `provider()`
   takes the capability it is being asked for.
   **Everything that follows from *which* provider is now answered per slot**: `capabilities_of` is
   the union of the two (with `local` aligning and a vendor drafting, both are true at once and
   neither provider could have said so alone), `sends_audio` and the dated list price are maps keyed
   by capability, and the page's confirm names the provider of the slot it is about to use — *"the
   audio of this track is sent to deepgram to write down what it hears"* while an alignment on the
   same track leaves nothing.
   **The panel offers only what a slot could use.** `OFFERS` says what a *kind* of provider can ever
   be asked for — Deepgram transcribes and nothing else, so it is not in the aligning list; `local`
   and `http` can do either, since what they can do today depends on what is installed and on the
   machine at the other end, which `capabilities()` answers at runtime.

41. ✅ Give the graphics card back (2026-09-27, P30, backlog 18). Found while measuring P27: the
   installed service, having aligned one track for the user, was still holding **2.9 GB** of an 8 GB
   card with an empty queue — enough to make another process's separation fail with an out-of-memory,
   which it did. A desktop app should not sit on a third of the card while doing nothing.
   **Two different situations, and only one of them needs a timer.** The app's own service builds a
   provider per job and drops it with the job, so its models are already gone; what lingers is
   torch's allocator pool, and `release_gpu_memory()` empties it the moment **no lane is busy**.
   `ytalbum timing-serve` is the other case: one engine for the life of the process, so it lets go of
   the models themselves after `timing_idle_minutes` of quiet (default 5; `0` means never, which is
   what a machine dedicated to serving this wants).
   **Only if torch is already in the process.** An installation with no timing provider must never
   import it, and importing 1.5 GB to free nothing would be the worst possible answer — so the
   release is a `sys.modules` lookup that costs nothing where no model was ever loaded.
   **A tidy-up on a timer has to know the thing is in use.** The first live run took the separator
   out of a *running* alignment (a six-second idle window against a ten-second job) and the request
   died with `'NoneType' object has no attribute 'samplerate'`. A request now holds the `Idle` while
   it works, and the watcher releases only when nothing is being served; the provider also keeps the
   separator in a local, because a cache another thread may empty is not a place to read twice.
   **Measured on this card** (`docs/qa-catalog.md`, section AB): 3314 MiB while the models are
   loaded, **180 MiB** once released — that remainder is the CUDA context, which belongs to the
   process until it exits. The next request reloads from a warm disk and costs nothing anybody can
   measure: 11.8 s before the release, 11.2 s after it.

42. ✅ Giving the words back (2026-09-27, P31, backlog 19). ytalbum takes its lyrics from LRCLIB's
   contributors; timing a song by hand, or checking what a model proposed, is work, and the way to
   repay it costs one request. **"↑ publish to lrclib"** in the lyrics panel, using their public
   publish API — no account, no key, a proof-of-work challenge instead (documented at
   <https://lrclib.net/docs>, read 2026-09-27).
   **Only your own work, and only what would be new to them.** The button appears for a sidecar that
   is the user's (`provenance.lyrics = user`), timed, not instrumental, not a machine's draft nobody
   has rewritten, not byte-identical to the LRCLIB entry whose id the track holds, and not already
   published with these same bytes. Every one of those is one idea — *a publish cannot be taken
   back, so every doubt resolves to "no"* — and each refusal says which it was, because a missing
   button explains nothing.
   **Said before it happens, in full.** The confirm names the artist, the title, the album, the
   **file's** length (the clock the stamps belong to, §9, slice 35), how many lines, that both the timed and
   the plain form go, and that LRCLIB is public and takes no account. The job log writes the same
   sentence before the request, so a log read later says what left this machine.
   **One press is one request.** The challenge may be asked for again — asking changes nothing — but
   the publish POST is never retried, because a retry could be a second copy of the same words in a
   public database. A refusal comes back as LRCLIB's own message, and nothing on disk changes, so the
   button is still there.
   **What is recorded is a fingerprint, not the words**: `lyrics_published = {"at": …, "sha": …}`.
   The same bytes are then never offered again; words edited since may go again, which is the only
   sensible reading of "I changed it".
   `YTALBUM_LRCLIB_BASE` points the client at another host — a mirror, or the fake that let this be
   verified end to end without putting test words into a public database.

43. ✅ Offering an album to MusicBrainz (2026-09-27, P32, backlog 20). The other half of giving
   back: ytalbum reads MusicBrainz on every fetch, and an album it cannot find there is an album
   nobody else can find either. **"Add to MusicBrainz"** on the album head opens *their* release
   editor with the boxes filled in, through the documented seeding format (a form POST to
   `/release/add`, read at <https://musicbrainz.org/doc/Development/Release_Editor_Seeding> on
   2026-09-27).
   **ytalbum submits nothing, and holds no credentials.** The page builds a hidden form and submits
   it into a new tab; the person is signed in as themselves, reviews every field, and presses
   MusicBrainz's own button — or does not. That is the whole reason this is seeding and not an API
   client: an edit belongs to the person making it.
   **Only what MusicBrainz would want.** Not a release they already have (the album has an `mbid`),
   not a compilation, not somebody's artist playlist, nothing undownloaded, nothing without a title
   and an artist. Where it is refused the head says which of those it was, in a line where the
   button would have been, because a missing button teaches nobody anything.
   **What is seeded** is the album title and artist credit, `type` (Album, or Single for a single),
   one **Digital Media** medium, the tracklist with numbers, titles and the lengths **measured from
   the files** in milliseconds, a track artist credit only where it differs from the album's, the
   year, the playlist URL, and an edit note saying where it came from and asking the person to check
   it. The `link_type` of the URL is deliberately left out: it is optional in their format, and a
   wrong guess is worse than the dropdown that is already in front of them.
   **The one thing seeding cannot do** is correct a recording, because the format is for releases. So
   where the file and MusicBrainz disagree about a song's length by more than ten seconds and they
   know the recording, **the length chip itself becomes the way to their page** — the chip that has
   always shown the two numbers (§9, slice 31). The first version of this put a second badge in the same row
   printing the same numbers with the opposite implication; one place for one fact.
   `YTALBUM_MUSICBRAINZ_WEB` points both at a stand-in, which is how this was verified without
   opening a real edit form.

44. ✅ Which method lost the song (2026-09-27, P33, backlog 21). §9, slice 38 could see that two aligners
   had placed a whole track differently and had no way to say **which** of them was wrong, so it kept
   the primary by policy. Now the answer comes from evidence where there is any, and the policy stays
   where there is none.
   **The signal is coverage of the singing.** A lyric's stamps should span the part of the track
   where somebody is singing; a method that lost the song squeezes the whole lyric into a fraction of
   it. Measured over eighteen real tracks (`docs/qa-catalog.md`, section AE): the five answers that
   lost the song span **0.41–0.70** of the singing, the twenty-seven that followed it span
   **0.86–1.12**. The rule is *under `LOST_SPAN` (0.75), or more than `LOST_PILED` (12%) of its
   stamps piled within a third of a second* — five right, none wrong, none missed, and any floor from
   0.70 to 0.85 gives the same verdicts, so the number is not load-bearing.
   **The decision, in three branches.** Exactly one method looks lost → the **other one's** stamps
   are kept whole and the notice names which lost and why, in the user's words: *"the two methods
   placed the whole track differently, and large-v3 is the one that lost it: its stamps cover only
   41% of the part of the track where somebody sings."* Both or neither look lost → exactly what
   §9, slice 38 did, keeping the primary and saying so. The per-line rule, where the two agree about the
   track, is untouched.
   **Two signals were measured and thrown away**, and both are still *recorded* so nobody has to take
   that on trust: "stamps where nobody sings", which cannot work because these tracks are 55–86%
   singing (a human's own stamps shifted by a minute land in silence only 12–30% of the time, and the
   method that was 120 s out on *Argent* put 0 of 46 stamps there); and each method's own confidence,
   which is not the same quantity on both sides and fails on its own terms — Whisper lost a track at
   0.60 and was right elsewhere at 0.07.
   **The library collects what sixteen tracks cannot.** Every cross-checked alignment a user saves
   records both methods' spans and piling figures in `lyrics_checked` beside the clock. Widening the
   thresholds is then done from real cases rather than from more of my copying: a read-only walk over
   the plans (`for _, plan in iter_plans(library): for t in plan.tracks: t.lyrics_checked`) collects
   every saved verdict, and a track a user reports as wrongly judged is a recorded case with its two
   spans already in it.

45. ✅ A draft that reads like a song (2026-09-27, P34, from the user's own test). Their first real
   draft came back as eleven lines for a 3:25 song, one of them a whole verse, with a minute of
   silence nobody was told about, under a notice reading *"11 of 11 lines came with a time"*. Three
   things were wrong, and only one of them was the vendor's.
   **A lyric's lines are pauses, not full stops.** Lines are built from the word timings: a pause
   over `LINE_GAP` (0.6 s) ends a line, a line may not run past `LINE_SECONDS` (8 s) or hold more
   than `LINE_WORDS` (12), and punctuation may end a line but can never hold one together across a
   pause. Where a line must still be split it splits **at its own longest internal pause** — cutting
   at the word count left a line reading just *"Rauch."*, which only rendering the real output
   showed. A vendor's sentences are now used for nothing except the case where it returns no word
   timings at all.
   **Silence is written down.** A stretch over `SILENCE_GAP` (6 s) with no words becomes a line of
   its own — `… (46 s without words)`, stamped where the singing stopped — because in an editor a
   hole looks exactly like an instrumental, and this was a transcriber missing every chorus.
   **The notice counts seconds, not lines.** *"Words for 1:01 of 3:38 of audio, with 4 gaps longer
   than 6 s marked in the text."* The old sentence was true of every draft ever made: of course each
   line has a time, the machine wrote them from times.
   **A transcriber listens to the voice, not the band.** Measured on that one song against LRCLIB's
   own lyric for the recording (`docs/qa-catalog.md`, section AF): Deepgram found 16 of 52 lines on
   the mix and **32** on the separated voice; the local decoder 28 and **38**. So a draft separates
   first wherever the `timing` extra is installed, falls back to the mix where it is not, and the
   notice says which it heard. For a paid provider this also means the isolated voice leaves the
   house instead of the record. A vendor draft is therefore at its best only with the local extra
   installed, which the README says in one sentence.
   And *more lines is not better*: on the mix the local decoder wrote 71 lines and found 28 of the
   reference's, writing words over the instrumental passages; on the voice it wrote 54 — almost
   exactly the reference's 52 — and found 38.

46. ✅ An entry that is nearly this recording (2026-09-27, P35, out of the user's own draft). A draft
   was offered for a song whose words LRCLIB had all along: the file is 218.06 s, the entry says
   213.68 s, and `TOLERANCE` is 3.0. The measurement that followed found **74 such tracks in this
   library** — and something larger: **71% of the entries *beyond* 3% of the file's length are still
   this recording's words** (`docs/qa-catalog.md`, section AG). The length gap is a poor proxy for
   "the same recording". The alignment is a direct measurement of it, and it answers two different
   questions at once: **how many lines it can place** says whether these are the song's words, and
   **how much of the singing they span** says whether the entry's clock belongs to this cut.
   **So length nominates and the alignment decides.** A candidate is nominated as today (same artist,
   same title, has words) with one cheap guard: within **±25%** of the file's length, so that an 83 s
   file never pays for an alignment against a 222 s lyric. Then one alignment sorts it:
   *span 0.85–1.15 with ≤10% unplaced* → take the words **and** the entry's timings, exactly as a
   within-tolerance match; *all the words placed but the span outside that band* → take the **words**
   and use **our** stamps, which that very alignment has already produced; *more than 25% unplaced* →
   reject, and remember it so no later pass proposes it again. Anything else is **unclear**: the panel
   shows both numbers and a person decides. Over this library: 143 taken whole, 16 with our clock,
   1 rejected, 29 shown, 14 never aligned.
   **What the tolerance does not become is wider.** `TOLERANCE` stays 3.0 and the exact endpoint is
   untouched: with no aligning provider nothing new is taken at all, and the panel says instead
   *"lrclib has words for this title, 4.4 s longer than this file — configure a timing provider to
   check them, or take them as plain text"*. A person without a GPU learns the words exist, which is
   the difference between "no words" and "no words yet".
   **Two paths to one sentence.** Beyond ±25% no alignment is spent and the panel says *probably* a
   clip or another cut, from the two lengths alone; within the guard, a span outside the band says the
   same thing with the measurement behind it. They are different paths and the words differ by that
   one "probably".
   **Ownership does not move.** Words taken this way are lrclib's — `lyrics_id` set, `provenance.lyrics`
   untouched — however they were taken, including by hand. Where our alignment replaced the entry's
   clock, `lyrics_timed_by` says so and the stamps carry the same caution as the align button's,
   because they are one method's word unless the check extra ran on them. `lyrics_fit` records the
   entry, both lengths, both numbers and the verdict, so a pass never asks twice and a user who
   disagrees has the evidence in the plan.
   **Cost, measured:** one alignment is 11.1 s on this laptop's GPU and 166 s on a processor, so a
   full pass over this library's 203 candidates is 38 minutes with a card and 9.4 hours without one.
   It belongs to a pass, never to an interactive lookup, and the panel offers it per track for
   somebody who wants one answer now.

47. ✅ A corpus that keeps the measurements (2026-09-28, P37). P27, P33 and P35 each ended with
   numbers in prose and raw material in a scratch directory, and one of those directories has already
   been deleted. So the measurements become tests: a **fast** half from the recordings that runs in
   every `uv run pytest`, and a **slow** half, opt-in, that does the work again on real audio and can
   catch what the recordings cannot — a model version, a resampling change, a device default. Every
   case asserts a **semantic outcome** rather than a number, and where a threshold decides it also
   asserts the range the threshold may move within, so a retune inside the measured gap passes and one
   outside it fails naming the gap. The other half is the **counterexamples**: the four heuristics
   that were proposed and disproved each have a case that must keep failing them, which makes the
   rule explicit — a new signal has to beat every counterexample before it can decide anything
   (`docs/regression.md`, `docs/qa-catalog.md` AH).

48. ✅ The plan format is pinned, and it tolerates the future (2026-09-28, P43). The plan is the only
   state (§6), so before a refactor touches it there is a corpus of real plans — one per shape the
   reference library holds, plus two built for shapes it does not — and a test that load-then-save
   loses no key and changes no value. **Byte-identity is deliberately not the invariant:** a plan
   written before a field existed omits it, and writing it back fills the default. Measured over 246
   real plans: 140 byte-identical, 106 differing, and **every difference additive** — nothing removed,
   nothing changed. `ytalbum plan --verify` reports that over any library and writes nothing.
   **One rule did change.** An unknown key used to raise `TypeError` on load. Two ytalbums share a
   library — the desktop app and a terminal — so a plan written by the newer one is read by the older
   every time it runs; refusing to load it is bad and silently dropping the field is worse, because
   the newer ytalbum would lose what it believed saved. Unknown keys are now carried through
   untouched and written back where they were. An unknown *schema* is still refused: tolerating a
   field is not tolerating a format.

49. ✅ The recycle bin, and one rule to replace three behaviours (2026-09-28, P47, spike
   `docs/spikes/2026-09-candidates.md` §4). **ytalbum never removes audio; it moves it to
   `<library>/.recycle/`.** Deleting a track, deleting an album and pruning what left a playlist all
   went through `unlink()` before, and they disagreed with each other: `delete_track` removed the
   kept original, `prune` left it behind (catalog E, phase 5). One rule settles both.
   **At the library root, not in the album.** An album folder can be deleted, and a bin inside it
   would go with the very thing it protects against.
   An entry holds the audio, the sidecar, the kept original, the tags as they were written, and the
   **whole plan track**, so a restore never has to reconstruct anything. Restoring is deliberately
   *not* symmetric: **the user's lyrics win** — a sidecar written while the track was gone is kept
   and the restore says so; **tags are rewritten by the ordinary pass**, not replayed, so a track
   restored after its album was renamed gets today's names; and a track the source no longer lists
   **comes back as it was**, for the next `prune` to move aside again, because restore undoes one
   action rather than arguing with the playlist.
   **A deleted album is recoverable as an album.** Its plan and cover are binned beside its tracks,
   because the source it came from may be gone by the time somebody regrets the deletion — so the
   folder is rebuilt from the bin rather than refetched. Restoring one track of a deleted album
   rebuilds the shell first; restoring into an album that was fetched again merges by video id and
   names what it skipped.
   **The order is: bin, then save the plan.** An interrupted delete therefore leaves the audio safe
   and the plan still listing the track — and `restore` treats that as a **repair** rather than
   refusing. The other order would leave a plan that had forgotten tracks whose files were already
   gone, which nothing could put right.
   **It never empties itself** — no age cap, no size cap, no sweeping. A bin that empties itself is
   one nobody can rely on, and the whole reason this exists is that the program makes judgements the
   user may disagree with. `ytalbum recycle empty` is the only thing in the program that really
   deletes audio, and it asks first.
   This is the groundwork for replacing a file with a better candidate (spike §3): nothing may be
   replaced until it can be taken back.

50. ✅ A track holds candidates, not a video id (2026-09-28, P48, spike
   `docs/spikes/2026-09-candidates.md` §1). `PlanTrack.candidates` lists every place a recording can
   be had from — `ref` (**opaque**: only its own provider may parse one), `provider`, the length and
   the quality measured from a file we actually have — with `chosen` naming the one in use and
   `refused_candidates` naming the ones never to offer again (the `lyrics_rejected` shape, slice 27,
   for the same reason: an answer somebody turned down should not keep coming back).
   **`source_override` folds in exactly:** overriding *is* choosing a non-default candidate. The
   reference library uses it once in 3946 done tracks, which said the idea was right and the shape
   was not.
   **The old fields are the truth.** `candidates`/`chosen` are derived from `video_id` and
   `source_override` on every load, and where a plan disagrees with itself the old two win. Two
   ytalbums share a library; the older writes `source_override` knowing nothing about candidates, and
   must not be silently overruled. This is also the synthesis step for every plan written before this
   slice, which is what makes the change additive (slice 48's invariant holds: 0 lost, 0 changed over
   245 real plans).
   Nothing ranks yet and there is still one provider. The shape exists so that choosing between
   candidates, and replacing the worse, have somewhere to happen (spike §3, §4).

51. ✅ Where audio comes from is one interface (2026-09-28, P49, spike
   `docs/spikes/2026-09-sources.md`). A pure refactor: nothing changed on disk but additive fields,
   and no behaviour changed. `sources.Source` names what the pipeline actually asks — **four
   required calls** (`collection`, `audio`, `probe`, `art`) and capabilities it may decline
   (`changed`, `listing`, `find`, `details`, `clean_entry`, `owner_artist`, `is_release`,
   `art_candidates`, `url_for`, `one_ref`, `handles`). The inventory said seven calls; it was right.
   **A ref is opaque** — only its own provider parses one — and after this nothing outside
   `sources_youtube.py` and `youtube.py` recognises a video id, a watch link, an `i.ytimg.com`
   thumbnail or an album-id prefix. A test greps for it.
   **Failures are types**, not strings: `Blocked`, `NoAudio`, `NotSupported`, `SourceError`, with
   the provider's own words as the message and `Failure` as the kind on disk. `service.py` compared
   against a YouTube sentence in three places and does not any more.
   **The classifier asks who owns a collection**, not which channel; the title conventions, the
   owner-to-artist reading and "is this a release" are capabilities the provider offers.
   Provenance keeps its strings on disk (`yt_music`, `yt_title`) under names that say what they mean.
   **Per candidate, not per album.** A track's audio resolves by its chosen candidate's provider
   (slice 50), the collection's own by the plan's — so one album can hold tracks from two, which a
   test proves with a YouTube album taking one track's audio from a second provider.
   `titles.py` is still reached into by `plan.py` for album-name hygiene; a test names that as the
   whole remaining coupling and fails if it grows. The intake folder (P51) is what will say what a
   provider without conventions should answer there.

52. ✅ **The rename to noaap** (2026-09-28, P50). New name, new repository, same history, same
   library. `smtws/noaap` was pushed the identical commits up to v0.9.0 and the rename sits on top;
   ytalbum gets a 0.9.1 with a deprecation notice and is archived. Version: **1.0.0**, because every
   public handle moves at once and because what 1.0 promises — additive-only plan format, the CLI
   verbs, the HTTP paths — is a promise slices 48 and 50 had already made keepable. `sources.Source`
   is explicitly outside it; P51–P56 will change its shape.
   **What the program is called, and what a thing on disk is called, are different questions.** The
   plan file stayed `.ytalbum.json` here: it is the format's name, and renaming it would have made
   every album noaap writes invisible to ytalbum 0.9.0, whose `iter_plans` globs for it — an
   invisible album is not merely unreadable, it gets re-fetched into a second folder beside the
   first. **Overturned in 1.30.0** (R-419): the only installations of 0.9.x left are this project's
   own, so nothing is reading for that name any more. The file is `.noaap.json`, `noaap migrate`
   renames the ones already on disk, and a folder that still holds the old name is named in a line
   rather than read.
   Likewise `.recycle`, `.originals` and `.parts`, which were never branded and did not move.
   **Three things answer to the old name, each read and never written**, so the old spelling fades
   instead of being maintained: the settings file (`~/.config/ytalbum/config.toml`, while ours does
   not exist — without it every command on a renamed machine says "set the library first"); the
   `YTALBUM_*` variables (ours wins, theirs works and says so once per name per process — and this is
   not only for the documented redirects, since the vendor keys are read from a file the user wrote);
   and the write header, because an installed PWA serves ytalbum's `app.js` out of its own cache
   until the service worker updates, and a 403 on every write is a poor way to find that out.
   A save always lands in our own directory, so the first setting changed is the last read of theirs.
   **The caches move without a fallback.** Read-both/write-new over two sqlite files is complexity
   for nothing when one command copies them; `noaap migrate` does, with the write-ahead files, and
   without it they refill (58 MB of lyrics, 15 MB of MusicBrainz on the reference machine, and
   MusicBrainz is rate-limited).
   **`noaap migrate` takes over without taking away.** Dry by default — the inverse of this
   program's own `--dry-run`, because it is the one command that reaches into someone's configuration
   and their systemd units. It copies and never moves, never overwrites what is already ours, and
   `--uninstall-old` removes only a unit file and a launcher entry, both of which ytalbum writes
   again on demand: that is the whole of the undo, and it is printed. The browser profile is never
   removed by anything, because it holds cookies and logins.
   **Both default to port 8765**, so `service install` checks and refuses with a sentence rather than
   letting systemd answer "Address already in use" from inside `enable --now`.
   Two defects were fixed on the way, both live: the MusicBrainz user agent named a repository that
   had been renamed — a wrong contact address on every request, and the one field they ask for — and
   both user agents claimed version `0.1` forever. They now read the installed version from one place.

53. ✅ **A folder is a source** (2026-09-28, P51). The second real provider, and the first that is
   not a website: an address is a path, a collection is a folder, `audio()` **copies**, and the
   source is never written, moved or re-encoded. Built against a real 43.8 GB collection —
   2000 files (mp3 932, flac 730, opus 338), 135 album folders, 12 top-level names, counted
   2026-09-28 — and every rule below was measured on it before it was written.
   **Four containers, one vocabulary.** FLAC and MP3 could not be read at all until now: everything
   that was not `.m4a` opened as Opus, raised, was caught, and answered `None`. A `None` length
   reads as "no match" and an empty quality as "unknown", so 1662 of those 2000 files would have
   arrived measurable by nothing, with 900 tests green. `kind()` is now the one place a suffix
   decides anything, and `keep_unknown=True` writes only the keys the plan asserts, leaving
   replaygain, ISRC, composer, BPM, a comment and an existing MusicBrainz id where they are — the
   difference between taking a collection in and taking it apart.
   **A ref is an absolute path**, because `audio(ref, into)` has one argument to find a file with.
   That has a consequence the boundary had not been asked before: **nothing derived from a ref may
   leave the program.** It is not written into a file's tags (`youtube_id` held the ref, so a
   folder track would have put someone's home directory into every file), it is never sent to
   MusicBrainz or LRCLIB, it does not appear in a fixture, and in a list or a tooltip it is shown
   by its last two parts — the whole path only in the source panel, where the user asked for it.
   A ref is an identifier only where the provider mints identifiers; everywhere else it is a way
   to find a file, and that is all.
   **Identity is the audio, not the file.** Hashing every byte of all 2000 files takes 41 s and
   finds **no duplicates**, because every copy differs in its tags; hashing the audio stream takes
   160 s (80 ms a file) and finds **68 recordings held twice** — a track on the album and the same
   track on the best-of. So `stream_sha` sits on the candidate beside `bytes`, survives a retag and
   differs between two encodings. Being the same stream never means discarding one.
   **What the filesystem states, and what it only suggests.** Numbered sub-folders (`cd1`, `CD 1`,
   `1` — three spellings, three albums) are the discs of one album, all of them or none. Sibling
   folders differing by a trailing `Disc N` are the one grouping the folder names only suggest, so
   the tags get the vote — and in the one real case they vote against it, because those files call
   themselves two different albums. **A folder says when it is one artist's release**: without it a
   normal album read as a compilation the moment one track credited a guest.
   **Tags and names are read apart.** `file_tags`, `folder_name` and `file_name` are three origins
   with their own provenance, weighted like the ones they stand in for, and a provider now names
   its own (`origins()`) instead of the planner guessing from its name. The 17 untagged files in
   that collection are one album named by this program's own output scheme, so their recovery is
   exact — and it surfaced an old defect: a title that *is* the album name stripped to `")"`.
   **Overlap is not this pass's decision.** An album whose artist and name are already in the
   library is reported with how many titles overlap, and left alone. Matching a file to a track we
   already hold is the same-recording question, and that is P52's first rule.
   **Not chosen is not copied**: an unchosen candidate records its ref, its measurements and its
   digest, and is fetched only if something later chooses it. Copying 43.8 GB of losing candidates
   to find out would be absurd.

54. ✅ **Which copy is better, and replacing the worse** (2026-09-28, P52). Two libraries meet: 246
   albums of YouTube Opus and 132 albums taken out of a 43.8 GB legacy collection. 2000 tracks
   against 3942, **762 pairs, 200 ambiguous, 1038 the library does not have.**
   **A container and a bitrate are claims; where the audio stops is a measurement.** One decode per
   file, ten 1 kHz bands from 14 to 23 kHz through ffmpeg's own filters, and the highest band still
   holding content is the file's cutoff. A band below −150 dB is not quiet, it is above that file's
   Nyquist, so every file is measured against its own ceiling: a CD rip reads 22 kHz and a full-band
   48 kHz file reads 24, and the CD rip is not the worse file — which is why `full` is compared
   before the number ever is. Residue is not content either: Opus leaves 40–50 dB of decoder noise
   above 20 kHz, so a band counts only within 30 dB of the 14–15 kHz band and above −80 dB absolute.
   Both numbers come from measuring all 338 Opus files in the collection, which carry nothing above
   20 kHz by design; at those two, none of them reads above 21.
   **What it catches:** a FLAC made from a 128 kbps mp3 reads 17 kHz, and a FLAC made from an Opus
   reads exactly what that Opus reads. **87% of that collection's 533 24-bit/48 kHz FLACs are
   band-limited at 20–21 kHz**, where 326 of its 338 Opus files sit. Sample rate does not decide and
   is only recorded: 44% of its CD-rate FLACs are cut at 19–21 kHz too.
   **What it cannot tell:** a quiet recording or an old master has nothing up there to judge by, and
   that is an answer — "no evidence" — never a bad score. An absent number never wins or loses a
   comparison; it makes the verdict undecided.
   **The order is the user, then identity, then quality.** A trim they marked, a source they chose,
   words they *timed* to this file: none of those is outranked by a measurement. Then ≤3 s is the
   same recording and >20 s is not, measured against MusicBrainz or LRCLIB where either knows the
   length, because two files can both be padded. Then a wider band decides — **by 2 kHz, because
   one kilohertz is inside Opus's own spread**, and a file at its own ceiling beats one that is not
   by any margin at all; a lossless *container* decides nothing; a rate decides only against the same codec, because Opus at 125 kbps and MP3 at
   320 are not ranked by their numbers; and a tie goes to the incumbent.
   **Verdicts over all 762 pairs: 51 replace, 0 fill, 423 keep, 288 undecided** — 1.93 GB added,
   0.22 GB binned, 14 albums touched. The largest single group is the **197 undecided where the
   incoming file is lossless and holds exactly the same audio**: under a rule that trusted the
   container every one of them would have been a replacement, and the 163 of them the first reading
   found already came to about 8 GB written for nothing. The second largest is the **134 keeps where
   the band is the same and the codecs are not**, every one an MP3 against an Opus.
   **The first reading of this material said 88, on a 1 kHz margin.** 37 of those were "21 kHz
   against 20" — and Opus itself reads 20 kHz on 219 of this collection's files and 21 on 107, so
   that difference is inside one encoder's own spread and decided nothing. At 2 kHz they are keeps
   and undecideds, and every replacement left can say why in a number wider than the encoder's own
   scatter (R-173).
   **The pass proposes and a person disposes.** A bare `merge` is a read — a case reads every byte
   and mtime of the target before and after and demands they are unchanged. `--apply` acts, and the
   only thing that removes audio is the bin, whose entry carries both files' numbers and the ref
   that displaced this one. Restoring is an undo: the copy that displaced it goes, the binned audio
   comes back, and the displacer is refused so the pass cannot propose it again.
   **Nothing derived from a ref leaves the program**, extended here to the page: it asks each
   candidate's provider for a link rather than building one out of a ref, because a folder's ref is
   a path on this machine.

55. ✅ **The copy nobody could choose, kept where a person can see it** (2026-09-28, P52c). The
   acceptance run of slice 54 found the hole in it: **288 of the 762 pairs were undecided, and the
   library held no trace of a single one.** The pass printed them once and exited. The panel that
   was supposed to let a person settle them could not, because *the page cannot show what the plan
   does not hold* — and so a verdict deliberately left to a human being was, in practice, a verdict
   nobody could ever take. A proposal a person cannot act on is not a proposal.
   **`--apply` now records the undecided copy on the track**: listed, not chosen, nothing copied and
   nothing binned, carrying the verdict's own sentence and both files' measurements. Both sides keep
   their numbers, because someone deciding between two copies needs what the report printed in front
   of them, not a second run of it. `Candidate.undecided` says so in the plan, and the plan format
   stays additive — an older noaap reads the field it does not know and writes it back.
   **Two answers end it and nothing else does.** *Take this one* makes it the track's ref by the
   ordinary switch — the audio is fetched again, the marks that belonged to the old file are named
   before they are cleared. *Not this one* is the refusal that already existed, remembered for good:
   it stays listed, marked refused, and no later pass offers it again. That is what makes the count
   trustworthy: `undecided_copies` can only fall, and only because a person answered.
   **So the library page counts it, the way it counts "needs you"** — the other question only a
   person can settle. Both are on the filter and both are a badge on the album, and the ⇄ mark on
   the row says a copy is waiting, because a number on a card that cannot be reached from the card
   is the same hole in a smaller shape.
   **A ref is still opaque here.** Only a ref already on the track can be taken: the user is
   answering the pass's question, not naming a new source, and parsing what someone typed stays the
   provider's job. A folder's ref is a path, so it is shown by its last two parts and never in full
   outside the panel the user opened — and, because `.originals` is keyed by the ref, a ref that is
   a path is turned into a name before it is ever written or read back as one. It was not: used as a
   glob it raised **in the middle of `bin_track`**, after the audio had been moved and before the
   entry was written, leaving a bin entry nothing listed and nothing could restore.
   **An album folder holds no audio its plan does not name.** Slice 49's sentence is *nothing is
   removed, it is only moved to the bin* — and half of it was being kept. A fetch that landed in
   another container wrote the new file and left the old one beside it: a player scanning the folder
   saw the song twice. Every switch now puts the file it displaced in the bin, with the usual entry.
   It is found **by the name, not by watching the extension change**, because those are not the same
   thing: `merge` renames as the file arrives, an `audio_choice` switch renames in the plan before
   the fetch is asked for, and neither leaves a trace the download could read. A switch never changes
   a track's name, only its container — so the same stem under another suffix is that track's
   previous file, whoever renamed it and whenever. The library root is **derived** from the album and
   the plan's folder rather than passed, because a parameter eight call sites can forget is not a
   rule.

56. ✅ **A file whose length nobody ever asked for** (2026-09-28, P52e). 574 of the reference
   library's 3946 finished tracks had a file on the disk and no `file_length` in the plan. Not
   because the file would not answer — every one of them answers from its own header in
   microseconds — but because **nothing ever asked**. `run` measures a track it has just written,
   and a track it finds without a length; `repair` is what would have reached the rest, and it
   `continue`s past an album whose names are already right *before* the point where anything is
   measured. In a tidy library that is nearly every album, so the gap could never close on its own.
   **The measuring moves in front of the skip**, and `update` does the albums it touches, saving
   before the fetch reads the plan back off the disk. `--dry-run` on either says how many and
   writes nothing — which for `repair` meant making the whole pass dry, since a run that writes
   "nothing except the renames" is not a dry run.
   **How it was measured is recorded too** (`file_length_by`: `header` or `decoded`). The two cost
   three orders of magnitude apart, and a decoded answer is a fact about the file — its own header
   would not say, which in 2000 files is three 24-bit FLAC albums and nothing else.
   **What it changed, honestly: nothing that is visible today.** All 556 such tracks in the
   disposable library already had the video's `duration` to fall back on, and the measured length
   differs from it by a median of 0.24 s and never more than 0.5 s — so not one ⏱ verdict changed,
   and no album's flag. None gains the near-miss check either: it runs on tracks with no words, and
   all 556 have them. What the pass removes is **a fallback standing in for a measurement**: the
   plan now records what the file is rather than what the video was, so a trim, a replacement or a
   re-timing has a true number to compare against instead of one that is right until the file is
   cut. That is the whole value, and it is worth saying that it is prospective.

57. ✅ **SoundCloud, and what a provider may decline** (2026-09-28, P53). The third provider, and
   the first whose **purpose had to be settled before the design**: SoundCloud is for music that is
   not on YouTube, not for better copies. Probed before a line was written — the label uploads this
   library is made of are **DRM protected** (yt-dlp is handed no formats at all; three of three
   tested), and an ordinary track offers **160 kbps AAC at best** (eight of eight). The one file
   fetched live measures **16 kHz**, against 20–21 for the same library's Opus, so slice 54's rule
   will keep the incumbent nearly every time. Nothing here touches DRM, ever.
   **What they share is a client that knows no site.** `ytdlp.py` holds the option dict, the logger
   and the first cut at an error message, and may not name a host, an id shape or an extractor.
   **Cookies are an argument there, never a lookup** — a shared helper reading `cfg.cookies_file`
   would hand one site's credentials to another the first time a second provider called it. The
   grep guard became three provinces, and SoundCloud's shapes were fenced off before a line of it
   existed. An error is shortened in **two** steps, because a provider reads the whole string for
   the phrases it knows and cuts it afterwards; one step would hide an age gate behind a sentence.
   **`LISTING` is not `SEARCH`.** SoundCloud can say what is on an artist's page and cannot find
   that page from a name — its own search returns tracks, never sets. **A provider does not declare
   what it cannot do**, so the capability split in two, and the refusal names the providers that
   can. There is no `find` here that raises; the method does not exist.
   **A set is read twice**, flat then full: one request more, and it buys what one cannot — when a
   track cannot be read, the flat list still knows its id and its place, so the entry is skipped by
   name instead of the album failing or a track vanishing. A SoundCloud track carries no album, no
   number and no year; the set is the only place they exist. A set on the owner's **albums** tab is
   a release and any other set is a playlist, which is the site's own statement about it.
   **Its titles are three rules**, each from a real page: a genre written for the search box, an
   `Artist - Title` prefix, and `(snip)`. `(Live)`, `(Acoustic)`, `(Remix)`, `(feat. …)` are kept —
   dropping those is how two different recordings become one. The set's *own* name is cleaned in the
   provider, because the core's album-name hygiene is YouTube's.
   **Two things only running it could say.** Every download failed `401 Unauthorized` because the
   address — yt-dlp's own listing URL with its escapes decoded — is claimed by **no** SoundCloud
   extractor and falls through to the generic one; nothing in the message says so, and the only tell
   is `[generic]`. And a preview is not the work: cleaning takes "(snip)" off, so a 40-second teaser
   planned as the song until the entry was marked unusable instead.
   **The ref is the numeric track id** and `url_for` answers None: a permalink can be renamed by its
   uploader, and the protocol would rather have nothing than a link that 404s.

58. ✅ **A collection becomes a library where it stands** (2026-09-28, P54). `noaap adopt <root>`
   writes **one file per album and nothing else**: the plan, beside the audio its owner already
   arranged. No file renamed, no folder moved, no tag written. That is enough for a library — the
   page, the player, lyrics, the length check and `merge` all work off the plan.
   **The reason it had to be built this way is a measurement.** Adopting the naive way — write a
   plan, mark the tracks done — and running one ordinary pass renamed every file into noaap's
   scheme **and renamed an mp3 to `.opus`**, after which our own tagger could not open it
   (`read b'ID3', expected b'OggS'`). `PlanTrack.ext` defaulted to `opus` and nothing ever set it
   from the file, because the download path fixes that when the audio arrives and for an album
   already on the disk the audio never arrives. Over the reference collection that is **1662 of
   2000 files** (932 mp3, 730 flac). So `Entry.ext` carries the container where a source can know
   it before fetching, and the core still never reads one off a ref.
   **`keep_names` and `keep_tags`** say the album is the collection's, not ours. `refresh_derived`
   returns without deriving anything — *that is where the wanting starts*, and a name derived once
   makes every later pass want to rename. `relocate` leaves the folder alone, the rename at the top
   of a run has nothing to compare against, and no pass writes into the audio: the words still
   arrive as a sidecar, MusicBrainz fills the plan, nothing is embedded.
   **Renaming and retagging are separate acts a person asks for**, and neither runs without a way
   back. `--retag` keeps every field this program does not model, because losing replaygain, ISRC,
   composer or somebody's own comment would be the adoption destroying the thing it took in.
   **The undo is judged on what matters, and says so.** Every file answers to the name it had,
   every field noaap would write has the value the file gave it, and the audio stream is the one
   that was there. The tag *block* does not come back byte for byte — mutagen rewrites it whole and
   a writer putting the same values back cannot put the same padding back — so byte identity is not
   the promise, and claiming it was would be a lie. An **absent** tag is restored by removing the
   key: putting the old values back while leaving our additions behind is not giving the file back.
   What noaap added is removed only while it is still what noaap wrote; a sidecar the user has
   edited since is theirs, whatever put it there.
   **The undo has to finish, and has to be able to run again.** Its acceptance run crashed on the
   one album whose files carry **no tags at all**: a Vorbis comment block has no `pop` — not even a
   one-argument one — so making a field absent again raised, and three containers' worth of cases
   had never reached that line because every file in them had a value to put back. The crash ended
   the whole pass, leaving 92 of 132 albums adopted and three renamed with no way back. So one
   track's trouble costs that track, one album's costs that album, what failed is named with a
   non-zero exit, and **when a track cannot be given back the plan is kept** — it is the only
   record of what those files were. A second run finishes what is left and leaves what is done
   alone.
   **Every file noaap writes records its own fingerprint as it writes it.** The first undo kept 80
   sidecars as "edited" that noaap had written itself, because it judged them against a snapshot
   taken at adoption — and a snapshot is stale the moment a later pass writes anything. A sidecar
   answers to `lyrics_sha`, a cover to `cover_fetched.sha1`, both maintained by the passes that
   write them. A file whose fingerprint was never taken is kept, not removed.
   **What the undo records is taken from the writers, not listed by hand.** The list was seven
   fields; `build_tags` writes fifteen. A retag added `tracktotal` and `totaltracks`, the record had
   never heard of them, and seventeen files came back carrying tags their owner never had — while
   the comparison that was supposed to catch it compared **the same list**, so it agreed with
   itself. The logical keys are now whatever `build_tags` returns with every branch turned on, each
   container in its own spelling, and a guard reads the writers' own source and fails when they
   gain a key. **A measurement that checks a list against the same list is not a measurement.**
   **Two things are refused rather than guessed:** a folder that already holds a plan, and a folder
   whose files say they are two different albums — the owner made that folder, and deciding which
   files belong together is theirs. And a replacement by `merge` in such a library takes the
   displaced file's own stem, because putting noaap's name on one file of an album called something
   else throughout is the one thing adoption promised not to decide.

59. ✅ **A folder that is watched** (2026-09-29, P55). `noaap watch` looks at the folders the
   config names and hands what arrives to the app. **It never does the work**: it notices, waits
   until the arrival has stopped moving, and asks for an ordinary job — the same one `fetch` or
   `adopt` would run.
   **It polls**, and the reason is measured. inotify is available through ctypes and this machine
   allows 254 776 watches against the collection's 226 folders, so the limit is not the problem;
   but **a full walk of the 2153 files costs 0.01 s**, and inotify cannot see what another client
   writes on an NFS or SMB share — which is where a music collection often lives. The settle window
   means seconds pass on purpose, so sub-second notice buys nothing.
   **The window belongs to the folder, not the file.** That is what carries an album arriving one
   track at a time: a second track starts it again and the album is handed over once, after the
   last of them stops. A file still growing never settles; a download client's temporary name is
   skipped by pattern and its rename starts its own clock; a file that vanished is a change like
   any other, noticed and never acted on.
   **Two shapes, and they are opposite in the one place that matters.** An *intake* folder is a
   stranger: an album already in the library is reported and left, because choosing between two
   copies is slice 54's question and a person's. A *library watching itself* is the album: a new
   file in it is a new track. They may not be nested, and the refusal says why — an intake inside a
   library takes every drop twice, a library inside an intake copies itself into itself.
   **And in a library, nothing is copied and no file name is written by a pass.** The obvious
   implementation — enqueue `fetch` on the album's own folder — was measured on a real adopted
   album and **wrote a newly dropped file over an existing one's name**: the provider names an
   entry by its *best* copy, so the new file changed that entry's ref, the merge read it as a new
   track, and the download half then placed it under the old one's name. So the library shape is
   `reread`, which aligns refs before merging: a fresh entry sharing **any** copy with a track we
   hold *is* that track, and the new file joins it as a candidate. A re-read also refuses an album
   whose source is not the folder it sits in — an imported album still belongs to where it came
   from, and reading it here made thirteen tracks into twenty-six.
   **Its own process and its own unit.** The web service is socket-activated and stops itself when
   idle; a watcher inside it would count as busy and keep it alive for ever. A separate watcher
   costs one idle process that stats a tree every ten seconds, and asking the service wakes it
   through that same socket. Its one way in is `POST /api/arrived`, which takes **a configured
   watch's name and a path inside it** — never an absolute path, resolved and re-checked against
   the root, refused for a name the config does not list, and a write like any other.
   **What it says, it says once**: a folder that is not there, an arrival the app would not take
   (three tries, then named and left), a loose file at the top that is not an album. What it writes
   down names the watch and the album inside it, never a path on the disk.

60. ✅ **A library that survives being moved** (2026-09-29, P55b). A plan writes a path that is
   **inside the album's own folder** as `./…`, and reads it back as the file it names. One marker,
   one rule: everything else — an intake folder somewhere else, a source that was never in this
   library — stays exactly the absolute path it was, because it is not this album's to rewrite.
   **The fact this exists for is not that a moved library breaks. It is that a moved library
   works — by using the files of the library it was copied from.** Measured on a real one: a
   1.5.0-style copy of four albums held 52 refs and **all 52 pointed into the original, none into
   itself**, every one of them on disk over there. So the copy played, measured and merged from
   somebody else's folder, and nothing said anything was wrong; deleting the original was the day
   it would have been found out. The same copy after conversion: 52 refs, all 52 relative, all 52
   resolving inside itself — and with the original moved away, all four albums re-read, 0 tracks
   added, every plan byte-identical. On the 132-album collection: 8396 refs, all relative, all
   resolving inside the copy, 0 outside it.
   **Only a plan that really holds one says `schema: 2`.** Every YouTube and SoundCloud album keeps
   schema 1 and stays byte-for-byte the file it was, readable by ytalbum 0.9.1 and by every noaap up
   to 1.5.0. A relative ref is what an older version cannot read, so it is the only thing that
   raises the number — and what raises it is an adopted collection, which no older version ever
   wrote. The reader takes 1 and 2 and refuses 3, as it always has.
   **The conversion is a load away.** Reading a plan gives what it always gave, so nothing that
   reads one had to change; writing it converts it. `noaap repair` walks the library and saves every
   album whose written form differs from its file, which is how one command converts a whole
   collection — 132 albums, 8396 refs, in one pass. `plan --verify` reports a conversion **as a
   change and not as a fault**, with its own count, and the three counts it prints are exclusive.
   **What a save writes is decided in one place.** `plan --verify` had its own copy of the rule and
   `repair` a third, and the third recomputed the schema line with a string search. Asking the
   round-trip question honestly then found a real defect behind it: a track synthesises the
   candidate for its own ref and must default it to YouTube, because a track cannot know its
   album's provider — only the load corrected that, so every `adopt --apply` left a folder album's
   candidates claiming YouTube in the file until something re-read and re-saved it.
   **Two absences are not one.** A track whose *own file* is missing is a broken library and is
   named as that. A track whose *source* has gone — an album taken in from a folder that is no
   longer mounted — is complete, and saying otherwise would tell someone their library is broken
   when only a re-fetch would be.
   **And a library that moved before it was converted cannot be converted where it stands**: its
   refs name the original's folders, which are not inside this album, so `repair` correctly leaves
   them alone and `plan --verify` correctly calls it byte-identical. Finding those files where they
   now are is its own question, and its own slice.

61. ✅ **A plan says where a track's file is, not only what it is called** (2026-09-29, P55b).
   `filename` is the track's path **relative to the album folder**. For everything noaap downloads
   that is a bare name and always was; for an adopted album it is whatever the owner's layout says,
   `cd1/…` included.
   **The defect this fixed is the worst one this project has produced.** Adoption recorded
   `Path(ref).name`, so for a track in a disc sub-folder `album_dir / filename` was not the file.
   Every pass then found the track's own file missing and fetched it again — which for a folder
   provider is a **copy** — into the album root under noaap's own naming scheme. One `noaap update`
   on a library that had **never been moved** wrote 64 files for the 67 tracks of three real albums,
   and three of those copies landed on each other's names, so two different recordings became one
   file with two tracks pointing at it. A later read of the folder saw both copies and appended them
   as new tracks: 24 tracks became 48. On the acceptance library, which took more passes: **102 files
   that the collection does not have**, against 67 of the owner's still identical in name, size and
   mtime. It breaks slice 58's whole promise — an adopted album keeps its names and nothing is
   copied into it — and it breaks it for the albums a collection is least likely to have a second
   copy of.
   **Slice 60's own check named it on the day and nobody read it.** Straight after `adopt --apply`,
   `noaap config` said *67 track(s) in 3 album(s) are not where their plan says*. An instrument that
   reports a fault nobody looks at has not found it.
   **What moved with the meaning:** a sidecar keeps its parent, so an `.lrc` goes into the disc
   folder beside its audio; the guard that answers "is this file inside the album" accepts any depth
   and still refuses a path that climbs out or is absolute; a replacement by `merge` stays in the
   folder the displaced file was in; what a fetch displaces is looked for beside the new file rather
   than in the album root; a restore recreates the disc folder if it went away; `--rename` renames
   **where the file stands**, because the layout is the owner's as much as the names were; and a
   fetched container that surprises the plan no longer derives a whole new name for an album that
   keeps its own.
   **The rule, and it is a test:** after `repair`, `repair --dry-run`, `reread`, the executor itself,
   `update`, `update --dry-run` and `update --deep`, the set of audio files in an adopted library is
   the set it had, **name for name**, and no `filename` or `adopted_name` has changed. 1245 tests
   passed over the broken behaviour; not one of them had an album with a sub-folder in it.
   **And an undo cannot take the 102 files back.** `added_by_us` lists the plan, the sidecars and the
   cover — never audio, because *the only thing that ever displaces a file is the bin* (slice 49).
   Nor can the record prove which file was the owner's: the old code wrote `adopted_name` as the bare
   basename, which is exactly the name the copy took in the album root, so in one album **24 of the
   35 root files answer to an `adopted_name`**. What is provable is that nothing was lost — every one
   of the owner's files is still in its disc folder, byte for byte — and that each stray decodes to
   the same audio as the disc file it was copied from. A pass that bins them on that evidence is
   **P55d** and not this slice. **1.4.0 and 1.5.0 are public**, so the next version's release notes say
   what happened, to which albums, and how to check: a defect that wrote into somebody's collection is
   not closed by a fix alone.

62. ✅ **The 1.0.0 promise, kept against a reader that does not know this version** (2026-09-29, P55b-2).
   The plan format is additive: a key is never removed, a value never rewritten by a newer version, and
   a key a newer version wrote is carried through untouched by an older one. **It was broken from
   1.1.0.** ytalbum 0.9.1 builds a candidate with `Candidate(**c)` — a bare constructor — so every
   field added to `Candidate` after slice 50 made the plan unreadable to it. Measured with 0.9.1's own
   code on the real library: **153 of 329 albums refused**, with
   `Candidate.__init__() got an unexpected keyword argument 'length_by'`. Slice 48's pass-through keeps
   an unknown key on the album and on the track, and **a candidate is neither** — so the newest part of
   the format sat in the one place nothing could carry.
   **The fix is where a candidate is written, not what it is.** The ten fields 0.9.1 knows stay inside
   the candidate; everything added since is written beside it, at track level, keyed by the candidate's
   ref, where `keeping()` carries it through whole. In memory nothing changes, the page and the API are
   untouched, and a value that still has its default is not written at all. The ten are a constant
   because they describe **another program's** class, which no longer changes; what moves out is derived
   from them, so a field added tomorrow goes to the compatible place on its own. Older plans are read as
   they are and written the new way on their next save; `noaap repair` does a whole library, by the
   mechanism slice 60 already had — *would a save write this file differently?*
   **Three things this cost, each of them the same lesson twice.** `plan --verify` compared the file
   against a plan it had parsed **raw** instead of loading, and so reported 116 real plans about to lose
   a `stream_sha` that a save puts back where it found it. `portable`/`resolved` rewrote values and not
   **keys**, and a map keyed by a ref is a map keyed by a path, so an undecided copy's own fields were
   dropped on the next read — the library page counted 0 waiting where there were 2. And the rewrite was
   **not idempotent**: handed a file already written this way it found nothing inside the candidates and
   removed the record of them. *One place decides what a save writes; one place decides what a load
   reads* — there were two of the latter, `load_plan` and `iter_plans`, and only one of them was fixed.
   **Verified live with 0.9.1's own code**, on a copy of a 329-album library it could not read: after
   one `noaap repair` it reads all 329 and refuses none, writes all 329 back, and afterwards the fields
   it does not know are still there, 178 undecided copies included, with nothing lost or changed.
   **A candidate's provider is written beside it as well, though 0.9.1 knows that field**, because it is
   the one field an older reader *rewrites*: on load 0.9.1 claims every candidate named by `video_id` or
   `source_override` for the album's own provider, so its save turned a folder copy inside a YouTube
   album into a YouTube one — 29 candidates in 15 albums of the real library — after which a re-fetch
   would ask YouTube for a path. Only a provider that **differs from the album's** is recorded, which is
   exactly the case claiming would destroy, and what is beside the candidate outranks what is inside it
   on load, so whatever an older reader did is undone by the next load here. Measured: after a real save
   by 0.9.1 of all 329 albums, **0 of 5372 candidates changed provider**, across the 33 albums that hold
   more than one.
   **And the two readers disagree about what belongs inside the candidate, permanently.** After a noaap
   save, 0.9.1's own `plan --verify` says *15 would lose or change something* —
   `candidates.N.provider 'folder' -> 'youtube'` — because that is what its save would do. Writing
   `youtube` there instead would silence it and **lie to every noaap from 1.1.0 to 1.5.0**, which reads
   that field verbatim and would then ask YouTube for a path itself. So the true provider is written
   inside, the older program's verify reports its own claiming, and nothing is lost either way: this is
   a disagreement about a value, not a field that goes missing. **The criterion is what noaap loads after
   an older version has saved** (R-217), and what remains true of those albums is said plainly in the
   README: an older version can read, play and save one, and loses nothing — but asked to *fetch* such a
   track again, it asks the wrong source.

63. ✅ **Taking back what noaap wrote into somebody's collection** (2026-09-29, P55d). Slice 61 stopped
   1.4.0 and 1.5.0 copying a disc-folder track into its album's root. The copies are still there, in a
   music folder that is not ours, and this removes them — **only where it can prove each one is a copy,
   and never by deleting it.**
   **Four conditions, all of them, or the file stays and says why.** The album is adopted and keeps its
   discs in sub-folders; the name is one the plan points at or one noaap's own scheme would write; the
   **decoded** audio is identical to a file in one of those disc folders; and that disc file is one the
   plan holds, which **the provider says**, because a ref is opaque and the core may not read a path out
   of one. A guard runs before the expensive test: a copy has its original's duration, so only files
   that agree to a tenth of a second are ever decoded.
   **The digest decodes, and that is the point.** `stream_sha` copies the stream instead, which is far
   cheaper and right for files nobody rewrote — but it called 14 of 22 real copies *different* from the
   files they came from (catalogue BD8). Deciding that a file may leave somebody's album is not a
   question to answer with a digest that can say that.
   **The name is evidence, and the ruling had it slightly wrong.** R-220 put the second condition as
   "its name follows noaap's scheme". The mechanism is that 1.5.0 copied to `album_dir / filename` and
   its `filename` was **the owner's own bare name**; on this collection the two nearly always agree,
   because ytalbum named most of it. Three real files prove the difference: their ID3 title is clipped
   at 30 characters, so the owner's name carries a bracket the scheme would never derive, and all three
   would have been left behind. A name the plan itself points at counts, and it is the stronger evidence.
   **Where the audio does not say which file a copy came from, nothing is done.** An album can hold the
   same recording twice; then the name decides, and where neither does, the file stays and the reason is
   printed.
   **A stray goes to the bin, with the file it was a copy of and the evidence** — the decoded digest,
   the length, and which of the two said its name could be ours. Nothing is deleted, by anything, ever.
   The tracks that existed only because of a copy go with it, and the owner's own track is pointed back
   at its file by the pass slice 61 already added, in the same run.
   **Dry by default** (R-220), which no other part of `repair` is: this one removes a file from a music
   folder, so `--strays` reports and `--strays --apply` acts.
   **Measured on the damage made with 1.5.0's own code**, on a 41 GB copy of the collection: adopt,
   `update`, and **64 files appeared that the collection does not have**. Then `repair --strays`: 64
   would be binned, nothing written; with `--apply`, 64 moved to the bin (507.7 MB), the three plans back
   to 24, 22 and 21 tracks all pointing into their disc folders, `plan --verify` 132 byte-identical and
   nothing missing. Then `adopt --undo --apply`: **0 failed**, where the damaged album had refused —
   and the tree is the collection again, **2000 files identical in name, size and mtime, nothing outside
   the bin that the collection does not have**.
   **What the collisions cost, exactly.** In one album three names exist on *both* discs, and each pair
   is two different recordings — so of 21 copies, 18 files remained, each holding whichever was copied
   last. Nothing of the owner's was lost: both recordings are in their disc folders, byte for byte. What
   was destroyed is a copy by a copy, and with it anything a user had done to the first one — a sidecar,
   a retag — before this pass ever ran. That is the part no measurement can give back.

64. ✅ **A cover noaap wrote, and how an undo can tell** (2026-09-29, P55d follow-up). `adopt --undo`
   removes what noaap added and keeps what the owner made, deciding by fingerprint: *the bytes we wrote
   are ours, anything else is theirs.* For a cover file that failed, and it failed **silently** — the
   undo said *"kept cover.jpg: it is not the file noaap wrote"* about sixteen covers noaap had written
   into a real library, and the album was therefore never given back completely.
   **Why: there was no record at all.** `run` saves the plan *before* it fetches the cover and then only
   again when a track changes something — which for an adopted album is never, because every track is
   already done. So `cover_fetched` died with the process while the file stayed on disk. Reproduced at
   1.5.0 and at 1.6.0, both leaving `cover_fetched: {}` beside a `cover.jpg` they had just written. The
   record is now saved the moment it changes.
   **Two proofs, and either is enough** (R-222): the hash the saving pass recorded, or **the picture
   inside the album's own files** — because a cover for a folder album *is* those bytes, handed over by
   the provider and written unchanged. That second proof is what recovers every library already written
   by 1.4.0 and 1.5.0, where no record exists: all sixteen real covers are byte for byte a picture in one
   of their album's own files.
   **What the second proof costs, measured before accepting it.** A cover the *owner* put there that
   happens to be byte-identical to the picture inside their own files would be read as ours and removed.
   On the reference collection that is **0 of 132**: its albums have either a cover file or an embedded
   picture, never both. The narrowing, if it is ever wanted, is to record at adoption whether the album
   had a cover file of its own — which would protect such a collection but recover nothing from the
   libraries already damaged, since they carry no such record either.
   **And a fingerprint is now a set.** `ours_still` takes every proof there is for a file and asks
   whether any of them holds; `lyrics_sha` is a 16-character prefix, so a proof shorter than a digest is
   compared as one. The record of a cover, the record of a sidecar and the picture in a file are the same
   kind of answer to the same question, and they belong in the same place.

65. ✅ **What an undo may delete, and what it may only set aside** (2026-09-29, P55d follow-up, R-223).
   Slice 64 gave the undo a second proof for a cover — the picture inside the album's own files — and
   with it a risk that had to be ruled on rather than measured away: **an undo removes by deleting**, so
   a wrong positive is somebody's own file gone for good. Three answers, and they are ordered by what
   each one costs if it is wrong.
   **What was in the folder when the album was adopted is recorded, and is never removed by any pass.**
   Every non-audio file, by its path relative to the album and its hash. It settles the hardest case
   there is — an owner's cover that is *byte for byte* the picture inside their own files — and it
   settles it without asking anything of the pass that later writes or removes files.
   **The weaker proof bins; only a recorded hash deletes.** A cover whose hash noaap itself wrote down
   is removed, because the record is proof. A cover recognised only by the picture goes to the bin, with
   an entry saying which proof found it and that noaap had recorded nothing — and it can be put back,
   which is the whole reason for not deleting it.
   **A library adopted by an earlier version has no record of what was there**, and for those the weaker
   proof is all there is. It still applies, still through the bin, and the run says how many files it
   could not decide about and left — as a sentence, not as one number among seven.
   Measured on the reference collection: **0 of its 132 cover files** would be claimed by the weaker
   proof, because its albums have either a cover file or an embedded picture and never both; the sixteen
   the undo had refused to give back are all of them the picture inside their own album's files.

66. ✅ **A track's file, found again by what it holds** (2026-09-29, P55c). A plan names a file. When the
   file is not there — renamed by its owner, moved into a disc folder, or the whole intake folder gone —
   the only way back is to ask what a file *holds*.
   **An identity is the digest of the decoded audio.** The cheaper `stream_sha` copies the packets
   instead, is three times faster, and agrees with it over every untouched file of the collection (2000
   files, 1932 distinct answers, 68 held by more than one file, the same 68 groups) — and it is still the
   wrong instrument. Measured to the byte on the pair that raised BD8: **7699 of 7700 packets identical**,
   the last one 52 bytes against 180, the difference being exactly the **128-byte trailing ID3v1 tag**
   that ffmpeg's demuxer hands over as audio data and that mutagen dropped when noaap tagged the copy.
   Stripping ffmpeg's own metadata does not fix it; digesting the per-packet md5s without their timestamps
   does not fix it. Decoding answers all 64 real pairs correctly, the three collisions included.
   So the packet digest survives as a **pre-check in one direction** — equal packets imply equal audio,
   never the reverse — and that direction is worth a great deal: **a renamed or moved file is byte for
   byte what it was, so the digest the plan already holds finds it with no decode at all.** Measured: a
   rename and a move, found with 0 decodes; 1 decode each on apply, to record the identity so that the
   next question survives a re-tag.
   **An identity carries its maker.** Another build of ffmpeg may decode a lossy file to other samples, so
   two are compared only when `audio_sha_by` agrees, and an identity from a decoder we do not have is
   measured again rather than trusted — and never read as a mismatch.
   **Cost, and where it is paid.** Not during a collection read: that would take an `adopt` of 2000 files
   from 167 s to 700 s for a question those passes never ask. The identity is measured the first time a
   pass needs it, written beside the candidate, and reused. `repair --find-moved` over 128 albums and 2100
   files: **7.7 s**, because only files that pass a length shortlist are decoded at all.
   **It never guesses, and it never touches a file.** Exactly one unclaimed match re-attaches; two, or
   none, and the track is named and left with the reason. A file another track already answers for is not
   a candidate — which is how the 68 duplicates are re-attached rather than left, each being claimed by
   its own album. And a file found in **another album** is named and *not* taken: `filename` is a path
   relative to the album folder, and a plan naming a file outside itself is what slices 60 and 61 exist to
   prevent. Found live, by doing it wrong first: re-attaching across albums left a plan naming a file it
   could then not find at all.
   **A whole album folder renamed needs none of this.** Slice 60 made every ref relative, so the plan
   travels with the folder: measured live, an album folder renamed by hand, 0 tracks lost, and
   `plan --verify` byte-identical afterwards.
   **A dry finding is a dry pass** (R-234). `--strays` and `--find-moved` are dry until `--apply`, and it
   was only *their own* half that held back at first: the rest of `repair` ran and saved every plan it
   tidied, so somebody who asked what would happen got a library that had been written to — a hash over
   132 real plans changed during a dry run. Without `--apply` the whole run now writes nothing.
   **The intake folder that moved** is the other half: an album taken in from one keeps its files here
   and its refs over there, so its own files are what find it again. All of the album in one folder or
   nothing: a provider names a collection by a folder. The provider reads that folder and mints the new
   refs; the core only says which file is which. Live, this refused once and correctly — two files under
   the new root held the same recording, because the collection holds that song both as its own album and
   as a track of another.

67. ✅ **A setter says what it changed, and a report writes nothing** (2026-09-29, P55c). `--library`
   means two different things on two commands: everywhere else it overrides the library for that one run,
   on `config` it **sets** it. I used `config --library PATH` as a read-only report and so rewrote the
   library root of the user's real installation, once per run, for a day — until they opened the app and
   found it empty. So any setter now names the file it wrote and prints what changed, from what, to what;
   `config` with no setter writes nothing at all, held by a case that hashes the file; and the help says
   which of the two `--library` is. On a fresh installation the old value shown is the **default**,
   because that is what the program was doing until now.
   **And the rule that followed, which is not in the code but in how it is run:** every command of a
   working session that reads or writes configuration, cache or state runs with `XDG_CONFIG_HOME`,
   `XDG_CACHE_HOME` and `XDG_STATE_HOME` pointed somewhere disposable. A program that keeps its settings
   where the user's are is one mistaken flag away from changing them.

68. ✅ **The line being sung stays in view** (2026-09-29, P57). The rolling lyrics list scrolled the
   active line **out** of view on every step. One arithmetic mistake: the page set
   `box.scrollTop = line.offsetTop - …`, and `offsetTop` is measured from the nearest *positioned*
   ancestor — which inside a table is the `td`, whatever the scrolling box does.
   **Measured on the installed page**, one track of 173 timed lines: in the editor's preview the box
   starts **645 px** below that `td` (the textarea is above it), so every target was 645 px — **28
   lines** — too far down, and the old arithmetic would have put the line outside the box on **166 of
   173** lines, by up to 571 px. In the read-only panel the same mistake is only **36 px**, less than
   the slack of a 254 px box, so there the line merely sits a line and a half high: **the editor is
   where it is ruinous, and the panel is where it hides.**
   The fix is a rect difference, which cannot be fooled by an offset parent, and a pure function that
   decides *where* — kept in `logic.mjs` so a node test can hold it. It **centres the line, keeps a
   line's margin above and below where the box is tall enough, and leaves a line that is already
   comfortably in view alone**, so the list does not fight somebody who scrolled it themselves.
   Verified live over whole tracks, in the editor and the panel, at 420 px wide and at 1600: **173 of
   173 line changes with the line fully inside the box, 0 px of overhang, 171 of 173 with a full
   line's margin** (the two without are the first and last, where the box is at its end), and **the
   page itself never scrolled**.

69. ✅ **Claiming a machine's draft as your own** (2026-09-29, P57). A draft is refused a publish, and
   the refusal said *write them yourself first* — **advice the app made impossible to follow**. The
   editor read `words_by` once when it opened and sent it back on every save, so a draft stayed a
   draft however much of it somebody rewrote. Nothing ever cleared the mark.
   **It is not cleared by editing, either.** One changed character of a machine's guess is not
   authorship, and a publish cannot be taken back — so it takes a statement, made once, deliberately:
   *I have corrected these words, they are mine*. Saved with it, `lyrics_words_by` goes; saved without
   it, it stays, whatever the words now say.
   **The refusal names that control, in the words the page puts on it**, and a case greps the page to
   keep the two the same: an instruction that names a control which reads differently is not an
   instruction. The badge *words by deepgram* goes when the mark goes and the *yours* badge stays,
   because whose the words are and whose the stamps are remain two facts — a claim says nothing about
   `lyrics_timed_by`, which keeps its provider.
   `publishable` is untouched. Verified live on the track the user reported: corrected and saved
   without the statement, still a draft and still refused; ticked and saved, the mark gone from the
   plan, the badge gone, *yours* still there, and the publish offered — which was never pressed.

70. ✅ **Patreon as a source** (2026-09-29, P56). Music a patron already pays for and can only reach
   while logged in. **Written and tested against fixtures only: it has never been run against Patreon**,
   and until it has, everything below is a design and a set of refusals rather than a measurement.
   **It cannot work without the patron's own session, and that part *is* measured.** yt-dlp's extractor
   uses Patreon's mobile user agent only when a `session_id` cookie exists for patreon.com, and asks for
   TLS impersonation otherwise — which this installation has not got and will not get (R-239, ruling 1).
   Asked anonymously for the **public** post in yt-dlp's own test list, Patreon answered
   `HTTP Error 403: Forbidden`. So there is no anonymous path to be tempted by: without a session every
   call refuses with the sentence that names the two settings.
   **A post is the collection, the campaign is the owner, a track's ref is the media.** Nothing on
   Patreon is an album: one post holding three files is three tracks, in the order the post lists them,
   and `is_release` is false for good. No track number is invented — a post title carries an episode
   number at most. A post address is therefore never one ref, because a post id cannot name one of three
   files.
   **An embed belongs to its own provider.** A post is often a YouTube or SoundCloud link with a note;
   the entry then carries that provider and that service's own id on its candidate, because the
   alternative is a ref only Patreon could resolve and Patreon does not host. An embed of anything else
   is left out by name — and **not turned into a Patreon ref**: a media id is digits, and a ref this
   provider cannot read back is not a ref (found by handing the providers in, below).
   **Nothing is stored but the setting.** Two fields of its own, defaulting to nothing, read by no other
   provider; SoundCloud's cookies are SoundCloud's. The cookie itself is never copied, logged or written.
   **The listing checks whose posts it was handed, and stops.** yt-dlp #10013 reported asking for one
   campaign and getting every membership the account had; the extractor filters now and this compares
   every post's campaign anyway, because the cost of checking is one comparison and the cost of trusting
   it is somebody's whole membership on their disk. The cap is **asked for** with `playlistend` rather
   than applied afterwards: reading two thousand posts and keeping 200 is rudeness with a filter on top.
   **Four failures, and which is which** (R-239, ruling 2): a lapsed session and a rate limit are
   `Blocked`, because they stop everything; a post the tier does not include is `NoAudio` — it exists and
   there is no stream for this listener, exactly as for a geo-blocked track, and the album goes on; a
   video post is `NoAudio` for that post, because the provider *can* read it and there is no audio in it;
   missing cookies are `NotSupported`. Anything nobody has seen before stays a plain `SourceError`
   carrying what Patreon said.
   **A provider does not build its neighbours** (R-241, ruling 1). Recognising an embed means asking who
   owns an address, and the providers to ask are handed in by whoever assembled them — nothing here
   constructs a provider, and nothing constructs a `Config` to construct one with. A case holds that by
   making both constructors raise.
   **The fixtures are written, not recorded**, because a real recording carries a patron's session in its
   URLs. A guard greps the whole directory for sessions, tokens, addresses and any Patreon page that is
   not invented — the one thing standing between a live run and this repository.

71. ✅ **The first live run against Patreon** (2026-09-29, P58). One campaign the user supports, read
   with their own Chrome session, `patreon_post_cap = 5`, under this session's own XDG directories.
   **Four defects, in the first four minutes, none of which a fixture could have shown.**
   **A setting the file cannot say is not a setting.** `patreon_cookies_from_browser` and
   `patreon_post_cap` were on `Config`, in the README's settings table and in the provider's
   `settings()` — and `load()` never read either out of the file. The run began with a configuration
   that did nothing. Every field of `Config` is now written into a file and read back by a case, so the
   next setting that is only half-added fails a test rather than a run.
   **A listing nobody shows is a listing nobody has.** `service.channel` grouped refs by a table of the
   three tabs YouTube and the folder source happen to use, so Patreon's `posts` refs were dropped out of
   their own listing: the campaign read fine and the CLI said *this channel has no releases or
   playlists*. Every tab a provider mints is shown now, known ones in their order, anything else under
   its own name.
   **A refusal is an answer, not a stack trace.** The first real fetch ended in
   `noaap.sources.NoAudio: this post holds video, not audio` — a traceback, because only `NotSupported`
   was caught at the top. Every failure a provider may raise is one sentence and an exit code.
   **A session that is set and does not work.** Chrome keeps cookies encrypted with a key in the desktop
   keyring; without `secretstorage` yt-dlp decrypts nothing, drops every `v11` cookie with a warning
   nobody sees, and the site answers as it answers a stranger. Reported now in `noaap config`, next to
   ffmpeg — reported, not enforced.
   **What the campaign actually answers** (and what the written fixtures had wrong): a flat listing is
   `{"_type": "url", "ie_key": "Patreon", "url": …}` per post and nothing else — no id, no title, no
   date, no campaign id. The post id comes out of the address; the title now falls back to the
   creator's own slug, because five bare URLs are not a choice. Note what the missing campaign id means:
   against this shape the #10013 filter has nothing to compare and drops nothing.
   **And what the creator turned out to be.** The user's words were *"use it, but it's not actually
   music or album shaped"*. All five posts are Mux HLS video — four `avc1`+`mp4a` renditions, no
   audio-only format, English subtitles, `has_drm: false` — narrated stories, an episode each, one
   `id` that is the post's own. So the provider refused all five, correctly by R-239 ruling 7, and
   the download half of this provider was untried at that point — it was exercised the same evening
   (slice 73). A post is still the right unit for a post holding audio; for this creator the honest
   answer is that there is nothing here to take *as audio*, not that the mapping is wrong.

72. ✅ **The audio inside a video post, and what a private source is** (2026-09-29, P59). The user's
   creator posts audiobooks as video, so the provider that refused every one of them was refusing the
   only thing that creator publishes. `patreon_audio_from_video` — **off by default** — copies the
   audio stream out of such a post and takes nothing else.
   **A copy, never an encode.** ffmpeg runs `-vn -map 0:a:0 -c:a copy`, so what lands in the library
   is the creator's own stream bit for bit; a codec no container on the list can hold is refused
   rather than transcoded, because a transcode is a different recording wearing the same name.
   **Chosen by the audio, paid for in picture.** The rendition is picked by its audio (bitrate, then
   sample rate) and, among equals, by the smallest picture carrying it. Patreon's Mux ladder repeats
   the same AAC at 270p and 1080p, so this is the whole difference in bytes and none in quality. The
   video lives in a scratch directory outside the library, is never written into it, and is deleted
   in `finally` — after success, after a failed copy, after a cancel.
   **A second ref shape, because the first could not be read back.** A video post's `id` is the
   *post's* id and the media API knows nothing about it, so the audio inside a post's video is
   `patreon:video:<post>` rather than `patreon:media:<id>`. It carries its post, which is why it is
   also the one Patreon ref that can offer a link a person can open.
   **Anything with protection on it is refused by name** and nothing is attempted against it: DRM on
   the post or on any rendition, a password, anything yt-dlp marked. A case replaces the option
   builder with one that fails the test if it is called at all.
   **And the rule the user asked for in the same breath** (R-250): *"i guess the authors wouldnt be
   thrilled to find those on musicbrainz"*. Nothing from a private source is offered to anyone — no
   publish to LRCLIB, no seed to MusicBrainz — and **no lookup either**, because a lookup sends a
   title, a creator and a duration to somebody else's server to ask a question nobody asked for. It
   is a **capability**, `sources.PRIVATE`, declared by the provider and enforced by the core: the next
   private source inherits the rule instead of being added to a list. The album's owner can turn
   lookups on for one album with `"lookups": true` in its plan, and nothing in the program ever sets
   it for them — a case greps the source to keep that true.
   **The live run was refused before it began**, and that is the result: Patreon answers `403` when
   Cloudflare's `__cf_bm` cookie has expired, which takes thirty minutes, so a session that worked an
   hour earlier fails with a login valid for another year (checked in the cookie store, names and
   expiry only). The refusal used to say *its session has gone stale*; it now names the one action
   that fixes either cause and claims neither. Taking audio out of a video post is therefore tested
   on written fixtures and **not yet on the live site**, which the README says in those words.

73. ✅ **One post fetched, and what the plan may not remember** (2026-09-29, P59b). The live run of
   slice 72 finally happened: the shortest of five posts, a 23½-minute narration, audio copied out of
   the video in 1 min 20 s — **16.6 MB kept**, aac 96 kbps, 44.1 kHz, length matching the source
   manifest to four decimals, cutoff 15 kHz, no video anywhere afterwards, and `update` over it
   changed nothing (same digest before and after). It also left three defects behind, and every one
   of them was found by somebody *looking at what the run wrote* rather than by the run passing.
   **A signed address is a piece of the session, and the plan had stored one.** The cover address of
   a paid post carries a token in its query; the plan kept it in `cover_url`. The rule was already
   written — *nothing of the session is stored* — and it had been read as being about cookies.
   **Decision: none, not the address without its query.** A de-signed address is worse than no
   address: it answers 403 for ever, it looks live to every pass that reads the field, and it would
   be retried on every run. A private album's plan holds no address; the cover is fetched from the
   read that is happening anyway, or not at all. The stripping lives in the one place that decides
   what a save writes, so a plan written before the rule is cleaned by the next save — and the guard
   is a grep over **every value** of a written plan for `token`, `Policy`, `Signature`,
   `Key-Pair-Id`, not a list of fields somebody has to remember to extend.
   **The cover failure was not what I said it was.** I reported that its token had expired; it had
   not — it was good for another two weeks. The address was fine and the *request* was wrong: an
   image address, handed back to the provider, was neither a post address nor a campaign address, so
   it went to yt-dlp's extractor as a page. A JPEG is not a page. Addresses of the media host are
   fetched as files now, with the session and a referer. And the warning says the reason: *could not*
   is not a reason, and the reason had been at debug level where nobody reads it.
   **A number that exists in a log nobody turns on does not exist.** The task asked for bytes
   downloaded beside bytes kept and the program could not answer, though both numbers had been
   measured. A provider may now say what its last download moved, the core asks with `getattr`, and a
   fetch that throws most of itself away says so on the track's own line.
   **And two more, from the review of that fix** (R-255). *A host is parsed, never searched:* the test
   for "is this our media host" was a substring, and it said yes to `evil.example/x.jpg?<our host>`
   and to `<our host>.evil.example` — a yes sends the browser's session and a referer to whoever owns
   them. It is now the parsed host, https only, equal to the media host or a sub-domain of it, and no
   unusual port; the address a redirect *landed* on is checked the same way, because the client
   follows redirects. *And an address nobody built is never handed to the client:* two paths still
   fell back to the address they were given — one of them reachable from a plan field a person can
   edit — so every entry point now resolves to an address this module built from digits it validated,
   or refuses. What a redirect target receives on its way is its own domain's cookies out of the
   browser, which nothing here can take back; that is exactly why the set of addresses handed over is
   closed.
   **A rule that leaves no way to obey it is half a rule.** *No address in a private plan* meant the
   album that had no cover file could never get one: the first fetch held an address in memory and
   every later run had nothing to try. A private album's cover is now asked of the provider, from the
   post itself, while a run for that album is happening anyway — never as a pass of its own, and a
   failure leaves the album without a cover and says why, once.

74. ✅ **The words that came with the recording** (2026-09-29, P60). A creator who posts an audiobook
   captions it, and those captions are the book. `patreon_captions` — **off by default** — keeps them
   beside the track and nowhere else.
   **They are a fourth kind of words.** Not the user's, not LRCLIB's, not a machine's draft of this
   audio: somebody else's writing, which arrived with the recording. `Provenance.SOURCE` says so,
   `lyrics_words_by` and `lyrics_timed_by` carry the provider's name, and the claim control from
   slice 69 is **not offered** — correcting a line of another person's text is not authorship of it,
   and a control that suggests otherwise is a lie in the shape of a checkbox. Editing them changes
   nothing about whose they are.
   **Sidecar only, decided in one place.** `build_tags` drops the words when the provenance is
   `SOURCE`, so no fetch, retag or `repair` can carry them into the audio file by another route — and
   because `signature()` reads the same function, such a track does not read as permanently out of
   date for words it will never hold. The spike had proposed a size limit on the tag instead; the
   ruling removed the question.
   **Two refusals that each stand alone.** Publishing is refused because the words are not the user's
   *and* because the audio came from a private source; a case proves each without the other, so
   neither is quietly doing the other's work.
   **What a cue is, and what is dropped.** WebVTT has two stamps per cue and `.lrc` has one, so the
   start is kept and the end is not invented. A cue's own line breaks are reading-width, not
   sentences, so a cue becomes one line. Speaker tags, styling, positioning, cue identifiers, `NOTE`
   and `STYLE` blocks are presentation and go. Overlapping cues stay, in start order: two people
   talking at once is two cues and dropping either would lose words. The reader knows no site — a
   format is not a source — and lives in the core with the provider handing over checked bytes.
   **Four refusals, each said once on the track's line.** Platform-generated captions (a machine's
   draft of the audio, not the creator's text), a file that is not WebVTT, a host this provider does
   not read, and — the one the spike insisted on before anything was built — **audio that does not
   start at zero**: the captions are timed to the whole asset, the copied audio is timed to itself,
   and a constant offset on every line looks right and is not. It is measured with `ffprobe`, not
   assumed, and an audio stream that cannot be measured is refused too.
   **A second media host.** The captions are served by the video platform (`*.mux.com`), not by the
   content host the images come from, so the parsed-host check of slice 73 now holds a pair — both
   checked the same way, and a caption address, which is signed, never reaches a plan.
   **Untried live, and said so.** Everything here is written fixtures: no caption file has been
   fetched from the real site, because that needs the owner's word.

75. ✅ **Whose words, and where the audio may go** (2026-09-29, P60b). Three defects the reviewer
   found by *using* the captions of slice 74 on a real album rather than by reading them.
   **A courtesy is not a rule.** The editor withholds the claim control for a creator's captions, and
   one save from that editor turned them into `Provenance.USER` anyway — the server set it without
   looking — after which they were the user's words and went straight into the audio file's tag. Now
   the server decides: words marked `SOURCE` stay `SOURCE` through any save, and `words_by` and
   `timed_by` stay the provider's whatever the request sends. **Clearing them gives the mark up with
   the words**, because what is gone is gone; whatever somebody writes from nothing afterwards is
   their own, `USER`, and still unpublishable while the audio is a private source's — which is the
   other rule doing its own work.
   **The audio, not just the title.** `align_lyrics` and `draft_lyrics` handed the file to whatever
   timing provider was configured, so an album somebody paid a creator for could be uploaded to a
   vendor for a transcript. It had been true since the timing providers existed and it shipped. The
   rule now: a private album's audio goes to a timing provider **only if that provider runs on this
   machine** — `local`, or `http` on a loopback endpoint, judged by the parsed host, because the
   timing server on the desktop upstairs is another computer however trusted it is — or if the
   album's owner turned lookups on for that album. Refused in the service, refused again at the web
   door before a job is queued, and **not offered by the page**, which asks the album and not only
   the installation.
   **And a stamp for a minute that does not exist.** `59.996` came out as `[00:60.00]`: the seconds
   were divided off and *then* rounded. Rounding to the unit that is actually written, before
   dividing, cannot do it.
   **The audit that came with it** (R-258, point 4): every route that can carry something of a track
   off this machine, and what each does for a private album — lrclib lookup and publish, MusicBrainz
   enrichment and seed, the Cover Art Archive (never reached, because it hangs off an mbid that
   enrichment would have had to set), the timing providers, the near-miss check, the local PO-token
   helper and the watch signal (both 127.0.0.1), and the page's own audio endpoint (the user's
   browser, on a loopback bind unless they ask otherwise). Separation runs in this process; a
   separated voice reaches a network only through the same timing door, which is now gated.

76. ✅ **Captions served as a playlist** (2026-09-29, P61). The live run of slice 74 refused the real
   post's captions, correctly and uselessly: `ext: vtt`, `protocol: m3u8_native`, an address ending
   `subtitles.m3u8`. The *segments* are WebVTT; the address is the list of them. yt-dlp's `ext`
   describes what the segments are, not what is at that address — which no fixture could have said,
   and one live run said immediately.
   **The playlist is read in the core and fetched by the provider.** A playlist is a format, like
   WebVTT; which hosts are ours is the provider's business. So `captions.py` turns text into segment
   addresses and joined cues, and `patreon.py` resolves each address against the playlist, checks its
   parsed host against the known pair, asks for it once, and stops at the first that is not ours —
   **a caption file assembled from two places is not this post's captions**, so one foreign segment
   refuses the whole track and nothing partial is written.
   **What `X-TIMESTAMP-MAP` is used for, and what it is not.** It says *this segment's `LOCAL` stamp
   is that MPEG-TS instant*, so a segment's own zero is `MPEGTS/90000 − LOCAL`. The first segment's
   zero is taken as the asset's zero and every later segment is shifted by its base relative to it.
   The absolute MPEG-TS origin is deliberately **not** trusted: it is the packager's clock, and the
   audio was copied out of an mp4 that carries no such stamps, so using it as an offset would be a
   guess dressed as arithmetic. Both conventions the format allows then work — identical maps with
   absolute stamps, and per-segment maps with stamps that restart — and everything that cannot be
   resolved with certainty is refused in one sentence: a map on some segments and not others, a map
   that cannot be read, and any cue that would land before zero.
   **Caps are request budgets.** 600 segments and 8 MB, constants of the provider rather than
   settings, one request per segment and no retry; over either, the track is refused whole. 600 ten
   second segments is an hour and a half of narration, and a program that would fetch two thousand
   files for one track's words has stopped asking whether it should.
   **And the cost is said before it is paid.** A dry run names the shape and the budget — it makes no
   request for captions — and the track's own line says afterwards what it actually cost: requests,
   segments, bytes, and **which convention the segments turned out to use**. The first live run could
   only infer that from the shape of its own result and could not say the bytes at all, though they
   had been counted for the cap; a number that exists and is dropped is the same defect as a number
   that is only in a log. None of it reaches a plan: it describes the transfer, not the words.
   **Measured live on 2026-09-29:** captions that came as a playlist, 49 requests, 341 lines whose
   stamps are ordered and free of duplicates, the last of them 4.08 s inside a 23:31 recording,
   27,903 bytes of `.lrc` beside the track — and not one word of it in the file, the plan, or
   anything this repository holds. **Inferred from that:** 48 segments, being the requests less the
   playlist. **Not recorded by that run:** which convention those segments used and how many bytes
   they were — which is exactly why both are recorded now, and why an earlier version of this
   sentence claiming "the same map on every segment" was wrong: it was read off the shape of the
   result. **And not known at all:** whether a line sits where it is spoken. Nobody listened.
   **Four corrections from the review of it** (R-263), all of the same family — *a list is judged as
   a list*: every segment must be a caption file **by its own first line**, so an error page or a
   truncated answer refuses the track and names which segment it was, instead of reading as a
   segment with no cues and leaving a hole in the middle of a chapter with nothing said (an *empty*
   WebVTT segment stays legal — it is a silence). Every address is resolved and checked **before any
   of them is asked**, so a foreign third segment costs no requests at all rather than two. A
   playlist using `#EXT-X-BYTERANGE` or `#EXT-X-MAP` is refused rather than half-read, because an
   entry that is a slice of another file is not a file, and so is the same address listed twice,
   which without byte ranges can mean nothing this program should guess at. And the request count is
   raised **before** the request, because a 404 is a request somebody's server answered.

77. ✅ **Eight things the user asked for, and what measuring them cost** (2026-09-30, P63).
   **A lossless copy that gives up nothing takes the place of a lossy one.** The user's rule, and only
   in that direction: candidate lossless, incumbent lossy, same recording, and the candidate's
   measured band not narrower — then it wins, because it can be re-encoded later without losing a
   second time and the file it replaces cannot. Raw kilohertz, without the 2 kHz margin, because the
   margin exists so that one encoder's spread cannot decide a *replacement* and here the band is only
   asked not to be an argument against. R-164 stands everywhere else: a lossy candidate never
   displaces a lossless file on the container, and a *narrower* lossless copy wins nothing.
   **A title and a recording are different permissions.** `lookups` lets a name and a length be asked
   of a stranger; for one package it also meant *upload the audio*, so an owner who had allowed a
   lookup would have had their paid recording sent to a vendor. `send_audio` is its own switch now,
   and an existing `lookups: true` plan sends no audio.
   **Two parsers, two answers, so neither shape is asked anything.** `https://evil.example\@host/…`
   is *evil.example* to a browser and *host* to this program's parser, and userinfo before the host is
   the older version of the same trick. Both are refused before the question of whose parser is right
   can arise.
   **A provider that would send nothing is not named as the one that would.** With nothing configured
   the refusal said "`none` would have to send the recording off this machine".
   **Two copies of one song get labels that differ.** Two libraries of the same layout give the same
   last two path parts, so the panel printed one line twice and only the numbers told them apart. The
   labels are made for the set, growing one part at a time only for the copies that collide.
   **And the number beside them says which number it is.** With a third opinion the distance measured
   is each file's distance from *that*, so two files of equal length could read "3s apart" — right
   number, wrong word. It says "3s from the length we know" now.
   **A length whose origin nobody recorded.** `file_length_by` was added after most plans existed and
   `run` measures only a track that has no length at all, so the field stayed empty on 4583 of one
   library's tracks against 559 with it. The tidy-up fills it from the header, never changes the
   number it describes, and leaves it empty where the header disagrees — because then the number is
   not the header's.
   **And the playback report, where the measuring was the work.** Every hypothesis before the
   measurement was wrong — a granule offset, an embedded picture, an idle-exiting server, a mistyped
   content type — and two of them *looked* confirmed until the experiment was repeated properly: the
   "strip the picture and it seeks" result came from comparing a fresh media element against a used
   one, which is the oldest trap in this document wearing new clothes. What the measurements
   actually found: clicking a timed line puts sound out in **8–117 ms** across fifteen clicks, and no
   multi-second stall could be reproduced in the reported shape; but **a jump past the trim end
   silently started the next track**, which from outside is the sound stopping and other music
   arriving. Playing *into* the end still moves on; jumping past it now stops where the audio ends.
   Three server defects came out of the same session: every file was served as `audio/ogg`, `HEAD`
   answered 501, and the responses were HTTP/1.0 — and switching to 1.1 immediately produced two of
   its own, a POST body left unread becoming the next request line and a 416 with no length leaving
   a client waiting. *A change that makes connections outlive one request makes every missing length
   a hang.*

78. ✅ **Asking the rule again** (2026-09-30, P63b). A rule changed — the user changed it — and every
   copy a pass had already listed was judged under the old one. `noaap merge --rejudge` asks the
   current rule about them, and **opens no audio file**: each listed copy carries the numbers the
   pass measured when it found it, so the question can be put again for the cost of reading the
   plans. Dry by default, like every merge.
   **What `--apply` does is what a merge does with that verdict and nothing more.** A `replace` goes
   through the same `_take` as any other: the displaced file to the recycle bin, both sides keeping
   their numbers, one plan saved per album. A `keep` means the copy is not worth offering any more,
   so it stops being offered — nothing is deleted and the sentence that settled it stays on the copy.
   **What it refuses to do:** a pair whose numbers were never fully recorded is left alone and
   counted, because re-measuring is two decodes and that is `merge`'s own job; and a track the user
   trimmed, chose a source for or timed their own words to is left alone with that reason, asked
   **before** the numbers are looked at — a track somebody worked on is not a measurement problem.
   A copy the user has already taken is not in the list at all, because taking one sets the track's
   `source_override` and `undecided_copies` stops listing it.
   **Measured on a copy of the user's installed library** (329 plans, no audio): 170 listed copies,
   **115 would become `replace`**, 45 unchanged, 10 left alone — 6 because the copy in use has no
   recorded measurement, 3 because the user chose that track's source, 1 because the listed copy has
   none. 115 tracks across 11 albums, and the run took 0.34 s for the whole library, which is what
   "opens no audio file" is worth.

80. ✅ **It was the handshake** (2026-09-30, P64b). Two requests settled what eight could not.
   Same cookies, same post, same query and headers, thirty-seven seconds apart, through Python's own
   standard library: with the **default** cipher list, `200` and 6491 bytes of JSON; with the cipher
   string yt-dlp pins on every connection (`networking/_helper.py`: `set_ciphers('@SECLEVEL=2:
   ECDH+AESGCM:ECDH+CHACHA20:ECDH+AES:DHE+AES:!aNULL:!eNULL:!aDSS:!SHA1:!AESCCM')`), `403` in 0.1 s.
   The bot check reads the TLS hello, and yt-dlp's hello is the unusual one.
   **What gallery-dl does below the headers: nothing.** Its Patreon extractor sets no `browser`, no
   `ciphers` and keeps `tls12`, so its adapter is built with `ssl_context=None`
   (`extractor/common.py`) — urllib3's default. The tool that worked was not doing something clever;
   this program was doing something unusual.
   **The fix is one documented option, and it is *less* shaping, not more.** `legacyserverconnect`
   takes the other branch of yt-dlp's own `make_ssl_context`, which sets OpenSSL's `DEFAULT` list.
   Nothing is made to look like a browser and no impersonation library is involved — the connection
   simply stops being customised. The cost is the other half of that branch,
   `SSL_OP_LEGACY_SERVER_CONNECT`, and it is confined to this provider.
   **Measured through the program's own reader**: refused at 01:13, 01:13, 01:14, 01:15 and 01:16,
   with every header set and both browsers; answered at 01:31 with this one option, four formats and
   the English captions, no browser opened in between.
   **And the advice that was true for a week is now false**, so it is gone: a `403` means the login
   has ended, and the sentence says *sign in again*.

79. ⏸ **Why this program is refused where another is not** (2026-09-30, P64 — measured, not built).
   The question: noaap needs the browser opened at patreon.com within the last half hour, and the
   user's own tool does not. Eight reads settled what it is *not*, and one comparison settled what it
   is.
   **Not the cookie arithmetic.** Firefox 142 writes cookie expiry in milliseconds and yt-dlp already
   divides by a thousand for schema 16, so the expired bot cookie is correctly seen as expired and
   never sent. Checked in the jar, not assumed.
   **Not the session, and not the browser.** The same Chrome session that had answered 49 requests
   three hours earlier was refused at 01:15; so was Firefox's. **Not the time** either, except as the
   life of one cookie: every refusal is a `403` in **0.3 s**, which is an answer, not a challenge.
   **Not the headers, and not the app version.** An app-consistent header set (`Accept: */*`, a
   referer, no browser navigation metadata) and the newer app version string that the other tool
   sends were both refused.
   **Not the HTTP client.** With `requests` installed, yt-dlp prefers its Requests handler over
   urllib — checked by asking the request director, which scores it 100 against urllib's 0 — and the
   answer was the same 403.
   **What did answer:** gallery-dl 1.32.14, on this machine, with *this* Chrome session, in the same
   minute, on the same post: `200`. Same cookies, same network, same app-family user agent. So the
   difference is in how that client speaks, below the headers this program can set through yt-dlp.
   **Why nothing was built.** The route that works is a second reader under GPL-2.0 against this
   program's MIT, which means a subprocess, a second JSON shape to map and keep mapped, and an
   external tool to document — and it would be built on an *unexplained* difference, which is how a
   fix becomes a mystery that breaks later. The cheap experiment that would explain it — replaying
   that client's exact query and headers through a plain HTTPS call with the same cookies — costs
   requests this package had already overspent. So: the advice to open the browser stays, because it
   is measured to work, and the next package gets the experiment.

81. ✅ **A line is placed when something supports it** (2026-09-30, P65). The user aligned an LRCLIB
   entry whose first four lines this cut does not sing. The result: those four pinned to 0.0, 53.2,
   53.7 and 55.8 s, the whole song after them ~4.8 s late, nothing objected, and the report said
   **placed 49 of 49**. Forced alignment places everything, because that is what forced alignment is:
   given words that are not in the recording it finds the least bad path for them anyway.
   **The evidence that works is whether anybody was singing there.** The vocal stem the aligner has
   already made says when somebody sings; a line is taken back when less than **a quarter** of the
   stretch it claims — its own span, or its first second, whichever is longer, so that a line before
   an instrumental break is not judged on the break — lies inside any of those stretches. Measured on
   the reported track: the four lines that are not in the recording scored **0.00, 0.00, 0.09 and
   0.64**; the forty-five that are scored **0.41 at worst and 1.00 in forty-four of them**.
   **The aligner's own score does not separate, and that is a measurement, not an opinion.** Those
   four lines scored 0.001–0.006, and four genuine lines scored at or below the worst of them
   (0.002, 0.003, 0.005, 0.006). A gate on that number would throw real lines away, so it is recorded
   beside the result — `line_scores` — and decides nothing. *A number that is there is not evidence
   because it is there.*
   **The rate is a backstop and never the first test.** Characters per second from one line's start
   to the next, measured over **128 263 line gaps** of the user's library: median 8.3, p90 14.7, p99
   25.7, p99.9 145, maximum 740 — and everything above about 60 is a stamp artefact rather than
   anybody singing, while the fastest lines that *are* sung sit at 50. So 60, and it only speaks where
   the first test had nothing to say.
   **What is left, honestly:** three of the four lines are taken back, and the fourth is placed where
   the singing really is — no per-line evidence tells it from a real line. That is what the second
   opinion is for, and its objection now reaches the editor: stamps that sit where nobody sings, or on
   top of each other, are counted and said in the notice, beside the reason each unplaced line was
   taken back. An unplaced line already kept its words and its place in the order and could be stamped
   by hand; that needed no change, and it got none.
   **What it is worth, measured against the version before it** (fifteen tracks by fifteen artists,
   the same set on both): no real line lost — over the fourteen tracks whose words fit, unplaced rose
   by **2 in about 650** (27 → 28 on one, 2 → 3 on another), placed lines still within two seconds of
   LRCLIB's own stamps; on a track whose words do *not* fit its recording (a median 15 s out) 0 → 3,
   which is the point of it; fifteen alignments in one process all ran and the card was free after.
   **And it is a partial fix, by its own case.** Three runs of the reported track, same words, same
   device: *(none, none, none, 55.3)*, *(none, none, none, 55.8)*, *(none, 54.1, 54.8, 58.4)* for the
   four lines that are not sung, against *(0.0, 0.5, 0.8, 55.8)* before. Two runs took back three of
   the four, the third took back one: the aligner is not deterministic across identical runs, and when
   it glues an absent line to the first sung stretch the evidence cannot tell it from a real line.
   A regression test that only counts what came back passes all three — so the count is not the thing
   to assert, the shift is (`docs/regression.md`).

82. ✅ **The app gives the graphics memory back** (2026-09-30, P66). Measured on the installed
   service: after one of the user's alignments it held **4320 MiB of 8188** while idle, and a second
   program that wanted the card failed with an out-of-memory. Every number below is from this laptop's
   card, taken before anything was written.
   **What one job holds:** 3608 MiB after one alignment of a four-minute track (peak 3460 MiB
   reserved); the aligner alone 494, the separator having separated 854, the second opinion **3776**.
   **`release_gpu_memory()` on its own frees nothing while a provider is alive** — the weights are
   still referenced, and faster-whisper's 3.6 GB is not torch's memory at all, so `empty_cache` has no
   claim on it. Only dropping the models gives it back: 3608 MiB → **160**, which is the CUDA context
   the process keeps until it exits. Loading every model again off a warm disk costs about **2 s**
   (aligner 0.7, second opinion 1.2).
   **So: hold the provider between jobs, and let it go when the work stops.** Built per job and
   dropped with it, `local` reloaded every model for every track; held, a pass over an album loads them
   once. The window is **60 s** (`timing_card_idle_seconds`, 0 = for ever) because that is the shape of
   the numbers: two seconds to undo, 3.4 GB to gain, and a minute covers a person working track by
   track. The old code released after every job *only if nothing else was queued at that instant*, with
   nothing to retry it — which is how a service ends up holding the card with an empty queue. A watch
   that looks every quarter-window cannot miss it that way.
   **The release and the check that it is safe are one step**, under the lock a job must pass to
   start: two steps leave a window in which a job starts and has its models taken away, which is the
   failure this was first built from (`docs/qa-catalog.md`, section AB). A queued job counts as work in
   hand, and a program that has done nothing since its last release does not release again — otherwise
   an idle app collects garbage once a minute for ever.
   **A full card is not a failure.** A job asks how much room there is at the moment it starts — its
   own loaded models counting as room, because it reuses them — and where there is less than it needs
   (3500 MiB to place words, 3800 to derive them) it runs on the processor and says so in its first
   line: the same track took **11.4×** as long there, which is slow but is an answer. Running out half
   way through is one sentence naming the cause and nothing else; a stack trace in the page tells the
   person waiting nothing they can act on.

83. ✅ **Words placed by listening first** (2026-09-30, P67). Slice 81 takes back a line that was
   placed where nobody sings; it cannot take back one glued to real singing, and on the reported case
   the fourth absent line is placed every time. The question forced alignment cannot ask is *were
   these words said at all* — so ask a transcriber with word times, then match the given lines to what
   it heard. **`listen`, a capability, asked of the drafting slot**, because the work it does is
   transcription and a vendor bills it by the minute; `local` and both vendors offer it, `http` does
   not yet.
   **The matching is string work and lives in the core** (`place_by_listening`): normalised words,
   `difflib` over the whole song at once so a chorus matches its three occurrences in the order they
   were sung, and a line is placed when at least **half** of its own words are found in one run —
   tolerating three heard words in between, because a transcriber drops words inside a line it
   otherwise has. Unplaced comes back with its reason: *not heard (1 of 6 words)*.
   **The threshold is not what limits this.** Between 0.3 and 0.7 the agreement of the placed lines
   does not move at all (79% within a second) while recall moves 49% → 58%, and the reported case's
   four absent lines are refused at every setting. What limits it is how much of the lyric the
   transcriber writes down the same way. Half is the middle, and it is a rule a person can check by
   eye.
   **Measured against the aligner on fifteen tracks by fifteen artists, 725 lines** (`docs/qa-catalog.md`,
   BV): the aligner places 96% with a median error of 0.27 s, listening places 57% at 0.49 s. On the
   reported case listening refuses all four absent lines where the aligner refuses three — and places
   36 of the other 45. So it is **a second action, not a replacement**, and the page offers both.
   **Two measurements changed the design as it was being built.** A transcriber left to detect the
   language wrote *27 words of Russian subtitle boilerplate* over a four-minute German song, so the
   given words name the language when two stopword lists can be sure of it (`language_hint`) — and say
   nothing when they cannot, because a wrong language is worse than none. And what is listened to is
   not the same for everyone: the local model does better on the track as it stands (412 lines against
   399, and 36 against **0** on the reported case, whose vocal stem lost the song), while a speech
   service over a band writes down *nothing* — five of six tracks came back from Deepgram empty, and
   the isolated voice took it from 8% of lines placed to 27%. So a vendor is sent the voice alone,
   which is also less of the record to send (§9, slice 45), and a listener on this machine is sent the
   recording.
   **What a stamp now says about itself:** `lyrics_timed_by` names the provider, the model *and* the
   method — `local/large-v3 (listen)` — because the same provider answers differently depending on
   which question it was asked. The words stay whose they were: listening writes nothing and claims
   nothing (§9, slice 21).

84. ✅ **A job holds only what it uses, and asks nobody anything it need not** (2026-09-30, P67b).
   Two defects the reviewer found by *using* the previous package, both measured.
   **Held for the next job is not held for this one.** A `listen` leaves 3.6 GB of the big model on
   the card. Slice 82's gate counted what the provider was holding as room — right for the next
   `listen`, which reuses it, and wrong for an alignment, which does not touch a word of it: the gate
   said "the card", the run started with 3355 MiB free where it peaks at 3460, and it died half way
   through with the out-of-memory sentence. Pressing the two buttons in turn is an ordinary thing for
   a person to do. So a job now lets go of what it will not use **before** the card is asked, and says
   so in its first line — *letting go of the big model: this job does not use it*. What each kind of
   job touches is one table (`USES`); the room that is counted is only what this job will reuse.
   Measured on a real server with the second opinion on, `listen → align → listen`: all three
   succeed, 29.9 s / 21.5 s / 29.8 s, peaks 4560 / 4448 / 4580 MiB, and the process holds
   3856 / 568 / 3940 MiB after each.
   **A local provider that needs no network must not use one.** With every weight already on the disk,
   the user's server log still carried *"You are sending unauthenticated requests to the HF Hub"*
   through jobs. Measured which load does it: not the aligner (torch's own cache asks nothing) and not
   the big model, but the **separator**, whose construction calls `hf_hub_download` for metadata on a
   file it already has. Every model load now happens with the hub switched off — the environment
   variable *and* the module attribute, because the library reads it both ways — and only a load that
   fails that way reaches for the network, saying once that it is downloading. Verified: 0 hub lines
   in a server log over the same three jobs that produced 2 before, and an opt-in test that loads all
   three models with `socket.socket` replaced by a raising stub.

85. ✅ **A dry run names everything the real run would do** (2026-09-30, P68). `repair --dry-run` said
   *"3946 track(s) would get the length of their file"* and *"246 album(s) would be tidied up"*, and the
   run that followed **rewrote 376 audio files** — on that dry run's word, the user had been told no
   audio file would be touched. The renames and the retags happen inside `run(download=False)`, which
   the dry branch returned before ever reaching: everything the pass does to *files* was outside what
   the dry run could see.
   **So the report is built from the same predicates the pass uses.** `would_do()` asks the three
   questions `run` asks — the wanted filename, the trim signature, the tag signature — and says one
   line per track: *would be renamed*, *would be cut to its trim points*, *would be retagged* with the
   values that change, old → new. A test compares the set of actions the dry run prints with the set
   the real run performs on a fixture album, so a future action cannot be added to one and not the
   other. Its own total is said as well, because "246 albums would be tidied up" is not an answer to
   *will this write into my audio files?*
   **Where two values differ, not where they start.** The first version of the report showed sixty
   characters of each side, which for a lyric printed the same text twice. It now names the character
   the two stop agreeing at — and that is what found the second defect.
   **The 376 files, to the byte.** LRCLIB ends a synced lyric whose singing stops before the track does
   with a bare stamp: `[03:52.92] `, trailing space and all. `update_track` wrote that text into the
   file's lyrics tag and into the signature beside it, while `read_sidecar` **strips** the text it hands
   to every later pass — so the signature never matched again and every tidying pass rewrote the file
   for one character. Measured: 52 of 52 stale opus files in the test library differed in exactly that
   character, 864 tracks in that library were stale for it in some container, and rewriting 18 of them
   on a copy made each file **exactly one byte smaller**. The text is stripped once now, where it
   enters, so the writer and the reader agree; files written before this get one catch-up rewrite,
   which the dry run announces.
   **Also in this slice, from the queue:** `http` can listen — `timing-serve` serves `heard`, the words
   come back over the network and the matching to the user's lines happens on the asking machine, which
   is the one holding the lyric. And two bits of noise measured to their owner: tqdm builds a
   *multiprocessing* lock the first time stable-ts draws a progress bar and keeps it for the life of the
   process, which is the *"1 leaked semaphore"* a server reported at shutdown (not ours, but
   `tqdm.set_lock` with a plain lock settles it), and those progress bars were painting themselves
   across the server log because stable-ts reads `verbose=False` as *"draw the bar, print no text"* and
   only `None` as *"say nothing"*.

86. ✅ **A cut file starts at zero** (2026-09-30, P69). The user, twice: *"when i jump around in the
   file after cutting the front 4 seconds it jumps to the next one right from the middle at times."*
   Fifteen measured clicks in P63 found no stall, because they were measured on files that were never
   cut. The reviewer found the difference: **every front-cut file in the library reads a negative start
   time** — `-0.900000` for a 4.9 s trim, minus the fraction of the trim point — because `-ss` before
   `-i` with `-c copy` keeps the packets before the cut point and marks them negative instead of
   dropping them.
   **Two defects, and the visible one is the page's.** Measured in Chrome in app mode, muted, on copies:
   a cut track began at **4.9 s into itself**, and when a track ended the *next* one began at 4.948 —
   which is exactly "it jumps to the next one right from the middle". The cause is not the negative
   start but the trim guard: the plan's trim points belong to the **original**, and the file on disk has
   already been cut to them, so measuring the playhead against them skips the head twice. A window
   already in the file is not a window: the guard now does nothing for a track whose file carries the
   cut, and still previews while the points are being changed. After the fix the same measurement reads
   *started at 2.475* (no skip) and the next track begins at **0.018**.
   **And the file itself.** `-ss` after `-i` drops the packets before the point and
   `-avoid_negative_ts make_zero` puts the first remaining stamp at 0.000 — measured: first packet
   `0.000000`, length within one packet of the trim span, no re-encode, and 14 KB smaller than the old
   recipe because the pre-roll is really gone. The cost is the 20 ms packet the cut point falls inside,
   which is the accuracy `-c copy` always had. The other candidate, `make_zero` with the old input seek,
   was **measured and rejected**: it shifts the clock but keeps the pre-roll, so the file still holds
   nearly a second of what the user cut away (190.85 s against 189.94).
   **The files already cut this way** are found by their own start time and cut again from the untouched
   original beside them. `repair` names each one in the dry run — *"01 would be cut again: its clock
   starts at -0.900 s"* — and says when it cannot, which is when no original is kept. Such an album's
   *plan* is perfectly tidy, so the pass had to be taught not to skip it: that is one `ffprobe` per cut
   track, and most tracks are not cut.

87. ✅ **The window belongs to the file the player loaded** (2026-09-30, P70). The user on 1.19.0:
   *"trim process is nice but after saving it starts as if the cut part was the original, starting the
   front trim into the trimmed part not into the original."* Measured, in Chrome in app mode on a copy,
   by walking their flow: the page's album object is **not refreshed after a short job**. The panel was
   rebuilt only on a busy → idle transition seen by a poll, and cutting one track takes about a second,
   so a job submitted at *t* and finished before the first poll never produces that transition. The page
   then still believes the file is uncut: it asks for the plain file — which by now *is* the cut one —
   and applies the trim window to it. Head skipped twice, which is the sentence above.
   **Three things follow, and the first is the invariant.** The window is applied only to the file the
   player actually loaded (`onTheOriginal`, set from the URL asked for, not from plan fields that can be
   half a save old). My slice 86 flag was the wrong shape: it asked whether the *plan* said the file was
   cut, and with a fresh page that meant the window was dropped for a track played from its original —
   the one case where it must run. I measured that as "no skip" without checking which file was playing.
   **Second, the audio URL carries the shape of the file** (`c=` — the trim it is cut to, or its length):
   a file that was just replaced is never reused from the media cache. Measured: after a cut the element
   went on reporting 194.85 s for a file that had become 186.23 s.
   **Third, a job of ours that changed the library refreshes the panel**, whether or not a poll saw it
   running — and the queue entries are taught what the fresh plan says, *without touching the source*.
   The element is in the middle of something the user started; a save is not a request to restart it,
   and replacing the source under a pending `play()` is what produced *"The play() request was
   interrupted by a new load request"* (R-295). So the sound carries on, the page learns, and the next
   start of that track loads the right file. Marks the user has moved and not saved are kept.
   Measured after the fix, the same seven steps: no load at all at the save, then the original with its
   window (10.69 s on a mark at 8.6), across a reload, and on a track cut before the page was opened —
   with no error and no interrupted-play message anywhere.

88. ✅ **A jump is answered where it was asked** (2026-09-30, P70b). The user on 1.19.1: *"every
   jumppoint completely restarts the track now, while the indicator and textlines follow the selected
   startpoint."* Walked in Chrome in app mode on a copy — a track cut before the page loaded and one cut
   in the session, three lines each, playing and paused, plus a click during a pending load and two
   clicks 80–120 ms apart: **21 clicks, every one landed where it was asked, none reloaded**. So the
   restart needs something this copy does not have, and that is said rather than guessed at.
   **What the measuring did find** is on the same theme as slice 87, one layer down: `/api/audio?o=1`
   **falls back to the cut file** when no untouched original is kept. The page believed it was holding
   the original, so it added the trim to every lyric stamp and applied the trim window — to a file that
   already carries the cut. Every jump then landed `trim_start` seconds late, and a stamp near the end
   of such a file lands past its end, where the element clamps and the queue moves on. The album payload
   says `original_kept` now, the page asks for `o=1` only when one is really there, and the offset and
   the window follow *that*. Measured on a copy with the kept original removed: a line stamped 20.00
   lands at 20.4 (before: 32.25, twelve seconds late).
   **Three rules the report asked for, each of which removes a way a jump could be lost.** A click on a
   line seeks the element that is holding this track and never reloads it — only a *different* track is
   started, and then the target is part of that start rather than a seek racing the load. `playIndex`
   moves the playhead only where a caller says: the unconditional reset to zero that slice 87 added for
   an already-loaded file is gone, because a lyric line asking for 1:30 must not be answered with a
   silent jump to the beginning. And the head-skip does not fire **after a jump**: somebody who asks to
   be before the trim start is asking for exactly that; playing into the head still skips it.

89. ✅ **A save reads the track rows, and a save that cannot be built says so** (2026-09-30, P70c).
   Found by the reviewer while measuring P70b: with a lyrics panel open, *"Save changes"* threw
   `TypeError: Cannot read properties of null (reading 'value')` and saved **nothing, silently** — no
   toast, no job. The page inserts its panels as extra rows after the row they belong to
   (`<tr class="lyrics" data-id=…>`), carrying the track's own id and none of its fields, and the save
   walked every row in the tbody. The first panel row it reached had no `[name=number]`, so it read
   `null.value` and the exception left the handler before `submit` was ever called.
   **A row is a track row when it is not a panel *and* carries the fields a save needs.** The second
   test alone would do; both are there so that a panel kind nobody has thought of yet cannot slip
   through. And the handler catches: a save that cannot be built now says *"This album could not be
   saved: …"* in the page instead of writing to a console nobody has open — measured both ways on a copy
   (before: no job, the title unchanged, the TypeError in the console; after: the job runs and the title
   changes, with a panel open or closed).

90. ✅ **What the first press of play was waiting for** (2026-09-30, P71). The user: *"initial play
   after refresh takes forever to start (like 10 seconds) on many tracks … afterwards everything's
   fine."* Reproduced on a generated library the size of theirs — 250 albums, 19 tracks each, real
   cover sizes — and the ten seconds were **two things, both the server's**.
   **Every request that named an album read the library until it found it.** `App.album` walked the
   plans: measured 6 ms for the first album, 83 ms for the middle one, **155 ms for the last**. A page
   load fires one cover request per card, so a refresh cost about **eighteen seconds of JSON parsing**,
   and the audio request queued in the middle of it — its time to the first byte measured at **5.5 s**,
   with the element giving up (`stalled`) after three. An index of `source_id → folder`, rebuilt when
   any plan file changes, makes any album **10 ms** wherever it sits.
   **And a cover was `Cache-Control: no-store` with no validator**, so every refresh downloaded every
   one again: **221 requests, 52 MB**, still arriving twenty seconds later. Covers are cacheable now,
   `Last-Modified` is sent for them and for audio, and a conditional request is answered with **304** —
   a revisit costs 3.6 KB instead of 52 MB. A **Range** request is never answered 304: a player asking
   for a piece it does not have must be given the piece.
   **The page helps too**: a card's cover is fetched when the card comes near the screen, by an
   observer. `loading="lazy"` was not enough — Chrome fetched nearly all of them anyway.
   **Measured after: the first press of play is 101–202 ms** in every state (cut with an original kept,
   cut without one, never trimmed, and a second press), in the installed app window and in a tab alike.
   **Two instruments came out of this.** A request slower than half a second logs its path, how long it
   took and how long until its first byte — which tells the two causes apart: *"/icon.svg took 4.93 s
   (4.93 s to the first byte)"* is a busy server, *"/api/cover took 5.29 s (0.00 s to the first byte)"*
   is a client that could not read it any faster. And **a client that left is not an error**: a media
   element abandons connections constantly and every one of them had been leaving a
   `ConnectionResetError` traceback in the journal since the server started speaking HTTP/1.1 (R-304).
   One debug line now; our own faults still come with their traceback.

91. ✅ **The recycle bin has its own place** (2026-09-30, P72). The user: *"i dont think the recycle bin
   should live in the settings view."* They are right: settings are what the program should do next
   time, and the bin is a place with their audio in it — one is a form, the other is a drawer. It is a
   button in the header now, beside the library's own two actions, **shown only when it holds
   something** and with the count on it; pressing it opens the bin as its own view. Everything the view
   does is what it did (list, put back, empty, sizes, reasons); only the place changed, and the three
   confirmations that told people where to look for a deleted track now name the header. The count comes
   from the state the page already polls, and it is **counted rather than listed**: one `iterdir` of the
   bin, where a full listing reads a `bin.json` per entry and measures its files.
   **And "Repair library" moves the other way, into the settings** — the user again: *"its function could
   be described better in there as 'repairing' could mean a lot of stuff and could also be dangerous."*
   It is described in full there (what it renames, retags, cuts, re-cuts and measures; that it downloads
   nothing, asks nobody, deletes nothing, and that a replaced file goes to the bin), and it is **two
   steps**: *Check* runs the dry pass of slice 85 — in the read lane, so it may run beside a download —
   and shows its per-track lines in the page; *Apply* is offered only once a check has been read. A check
   taken before something else wrote to the library is **stale** and says so instead of being applied,
   and what decides whether there is anything to apply is the albums the pass would touch rather than
   the number of lines it printed, because the summary line is always there.
   **"Update library" left the header too**, on the same word from the user. The header now holds what a
   person reaches for without reading — the search, the bin when it holds something, the theme, the
   settings — and the settings view has a **Library** section where both actions can say what they do:
   *"Check the sources for new tracks"* (with *Read every album in full* beside it, which used to be a
   shift-click nobody could see) and the repair. The two library actions were a modifier key and a word
   each; they are a paragraph each now.

92. ✅ **Only what is needed, and the sources you can name** (2026-09-30, P73). The user, of the
   settings: *"timing provider details only need to be visible when they are needed (provider selected)
   and how do i configure alternative sources, like another local or nas folder for merge, patreon,
   others?"* Both halves are the same complaint: the panel asked for things nobody had chosen to need,
   and did not ask for the things somebody did.
   **A timing field is shown when the provider of its slot uses it** — the endpoint for `http`, the
   device and the second-method check for `local`, one API key per vendor actually chosen — and the two
   slots are asked for together, so a machine aligning locally and drafting with Deepgram sees three
   fields and no endpoint. Switching a provider shows and hides them at once. A hidden field is **kept,
   not cleared**: the text stays for when it comes back, and what is not shown is not sent, so what the
   config holds for it is left alone.
   **Sources.** A provider that needs an account of its own is configured in the page: a session from a
   browser you are already logged in with, or the **path** of a cookies file — the path is all that is
   kept, the file is read on this machine while a download runs, and its contents never reach the page.
   The section is built from what the server answers, and the server builds that from the registry and
   the provider's own field names, so nothing in the core names a provider (the grep of §9, slice 73
   still passes) and the second such source appears the day it registers itself. Patreon also offers
   *take the audio out of a video post* and *keep the post's captions as lyrics*.
   **Take in a folder** answers the other half: a path — local or a mounted share — and the two ways the
   command line already offers. *merge* compares that folder with this library track by track and copies
   in what is better, writing nothing on the other side; *adopt* writes one plan per album beside the
   audio and nothing else, with that folder as its own root, because a plan says where its album is
   relative to the library it belongs to and this folder is not in this one. It runs **as the repair
   does**: *Check* is a read and shows the pass's own `report()` lines — the same lines `noaap merge` and
   `noaap adopt` print — and *Apply* does exactly what that check listed, offered only while the folder
   and the way of taking it in are still the ones that were checked. A path that is not a directory, or
   is the library, inside it or holds it, is refused with a sentence from the same rule the door and the
   pass use.
   **Watched folders are editable here**, both shapes the config allows, judged by the same rules before
   anything is sent and written back as `[[watch]]` tables with every other line of the config kept. The
   page keeps saying what it always said: the watcher is **a separate service** you start yourself, and
   whether it has been here.


93. ✅ **The service runs a release, not the working tree** (2026-09-30, P74). Found by the user on the
   day slice 92 shipped: their settings view was *mixed* — a page from the new commit against an API
   from the old process. The installed service ran an **editable** install (`noaap.pth` →
   `~/noaap/src`) and the unit started `~/noaap/.venv/bin/noaap`, so the page files, which are read per
   request, changed the instant a file was saved, while the running process kept the code it started
   with. The milder half is that mixing; the dangerous half is that this service **restarts itself** —
   `--idle-exit 900` behind socket activation means the next request after an idle window starts a new
   process from whatever the tree holds at that second, half-edited and possibly not importable. The
   user's app was one idle window away from running a scratch state.
   **The unit names a venv holding the released wheel.** `~/.local/noaap-release`, and
   `noaap service install` points at it whenever it exists — the same command moves an existing unit
   there — while `--from-checkout` is the deliberate way back for development. The watcher's unit
   follows the same rule, because `Restart=always` would otherwise pick a tree up faster than anything.
   **`scripts/release-install.sh` is a script and not a subcommand**, because the thing being replaced
   is the venv a subcommand would ship inside — a process overwriting the files it executes from — and
   because it must be able to install a tag whose own code predates the procedure. It builds from the
   **tag**, extracted with `git archive` into a temporary directory, so a dirty checkout cannot leak
   into a release; it refuses a dirty tree, an untagged commit, and a venv outside `$HOME` or in a
   temporary filesystem; and it prints the version the restarted service reports. That last part is
   possible because **the state now says which code is answering**: `settings.version` and
   `settings.running_from`, shown in the settings view as one line and by `noaap --version` as the same
   sentence — a release install says only its version, a checkout says which commit it is. The user
   asked for the version in the settings; the honest answer had to include which of the two it is.
   Measured while designing it (I-211): the wheel carries all seven page files; a lean install takes
   0.18 s into 51 MB; and a release venv costs tens of megabytes of real blocks under `$HOME`, where
   uv hardlinks from its cache, against ~7 GB on another filesystem, where it copies — which is why
   the script refuses `/tmp`.
   **And the installer brings what the runtime needs and the package does not declare** (P74b, found by
   the reviewer on the first real release): `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`, because
   faster-whisper runs on ctranslate2, which is built against CUDA 12's libraries while torch brings
   CUDA 13's — without them the second opinion falls back to the processor, some 11× slower — and
   `secretstorage`, without which yt-dlp cannot read Chrome's cookies at all. These are not
   dependencies: each is true of *this kind of machine*, not of the package, which is exactly the kind
   of thing a release install has to carry and a `pyproject.toml` cannot say. The wheel and the helpers
   are resolved in one install, so a helper that cannot be had fails the release instead of leaving a
   service that quietly runs slowly.


94. ✅ **The line you are stamping stays in sight** (2026-09-30, P74c). The user: *"‘stamp this line’
   (⏱) loses the focus on the line, while the nudge buttons keep it. So stamping line after line from
   the keyboard breaks after the first."* Focus was never the thing. Measured on a 34-line lyric in an
   8-row editor: twelve stamps walked the caret from line 1 to line 13 with `scrollTop` at **0** the
   whole way and `document.activeElement` the textarea every time — so from the eighth stamp on, the
   line being worked on was below the fold and the cursor invisible. **A programmatic selection does not
   scroll a textarea**: Chrome scrolls to the caret for typing and for the arrow keys, not for a
   `setSelectionRange`, and every control in this editor sets the selection itself. The stamp is the
   only one that moves the caret to *another* line, which is why it alone looked broken and why the four
   nudges — which rewrite one line and put the caret back on it — always felt right.
   `scrollToLine()` answers where the box has to be, keeping one line of context where there is room for
   it, and `showLine()` is the one place that focuses, sets the caret and applies it — the stamp, the
   nudges and the aligner's return to the top all go through it. Verified the same way it was found: the
   same twelve stamps, the caret in view at every one of them (`scrollTop` 0 → 25 → 65 → 106, moving
   only when it must), the nudge unchanged.
   Kept deliberately at the time: **the stamp still moves to the next line** — and **the user reversed
   that on 2026-09-30** (P79): *"setting a timestamp still sets focus to the next line. usually you have
   to finetune it, so it should stay on the line you just set."* The caret now stays on the line it
   stamped, and at the same place in its words, so the four nudges and ▶ act on what was just written
   without moving about first; the down arrow moves on, as in any editor. What this slice found stands
   unchanged — a caret the app moves has to be brought into view, which is now what happens when a
   stamp lands on a line the box has been scrolled away from.


95. ✅ **Sources are a list, and each one's fields are its own** (2026-09-30, P75). The user, looking at
   what slice 92 built: *"sources as cards or rows that can be added and removed and have their own
   config dialog once you add or edit them, so you have all sources together visible and do not have
   each source's settings mess exposed right away."* Slice 92 had answered "how do I configure another
   source" by putting **every field of every provider** on the page at once — a paragraph and four
   controls per source, before knowing whether the user has that source at all.
   Now the section is a **list of what is set up**: one row per provider, with a line saying what it has
   (*session from Firefox · audio taken out of video posts*), an Edit and a Remove; *Add a source* offers
   the ones that can be set up and are not. A provider is a row when something about it is set — a
   session or one of its switches — and `post_cap`, which is 200 out of the box because nobody chose it,
   is not evidence. Edit and Add open **one dialog for one provider**, with its fields, the sentences
   slice 92 wrote, and nothing of anybody else's; Remove clears that provider's keys after a confirmation
   that names what goes, and says what still works without a login.
   **Nothing about the config changed**: the same flat keys, the same file, and a config written by hand
   shows up as a row — verified on a hand-written `config.toml` and by a case that posts every
   `<provider>_<field>` one at a time, as a dialog's Save does, and checks that clearing one provider
   leaves the other exactly as it was.


96. ✅ **The box grows, and the last line is stamped once** (2026-09-30, P77). Two things found while
   measuring slice 94, neither of them what that package was about, both ruled on afterwards.
   **The editor's height was computed when it opened**, so a lyric written into an empty editor kept the
   smallest box — eight rows — however long it grew. It follows what is typed now, up to the
   twenty-six rows a full lyric gets, and **never shrinks while the editor is open**: a box that jumps
   back when a line is deleted moves the words somebody is reading. The growing is immediate; the
   preview under it still waits for a pause in the typing.
   **`tapped()` clamped the caret to the last line**, so pressing ⏱ again at the end of a lyric rewrote
   the stamp it had just written with a later moment. Somebody stamping downwards ran off the end and
   silently spoiled their last line, so the last line took its stamp once and a further press said
   *that was the last line*.
   **Superseded on 2026-09-30 (P79), by the user's own word** — see slice 94: the caret does not move
   to the next line any more, so nobody runs off the end, and rewriting a stamp is the point rather
   than an accident. Every line may be stamped again, the last one included, and the refusal is gone.
   What remains of this half is the reasoning: a clamp that turns "move on" into "do it again to the
   same line" is a silent fault, and the answer was to stop moving at all.


98. ✅ **A watched folder is a source** (2026-09-30, P80). The user, of the list slice 95 built:
   *"intake and watch folders are sources as well, and there might even be multiple of those."* They
   are, and they were in a section of their own under **Library** — among the *actions* — because that
   is where the watcher's status had always been shown. A folder somebody drops music into is where
   music comes from, exactly as a Patreon account is.
   Sources now lists both kinds: a provider is at most one row, a watched folder is **a row each**, in
   the order the config holds them, saying which shape it is, where it is, and what the watcher knows —
   *last looked at …*, *not looked at yet*, *the folder is not there*, or how many arrivals it could not
   hand over. *Add a source* offers a folder **always**, because there may be any number, and the
   providers only while they are unset. A folder's dialog holds its name, its folder and its shape, and
   applies `watchTrouble` to the **whole set** as it is typed, so a name another folder has or a nesting
   is refused in the dialog rather than by the door.
   **Take in a folder stays under Library**: taking a collection in once is an act, not a place music
   comes from. And nothing about the config changed — the same `[[watch]]` tables, written by the same
   writer, with a hand-written config coming back as rows; a case holds that writing the same watches
   twice writes the same file, and that appending a third leaves the first two tables alone.


99. ✅ **Precautions, before a collection that is somebody's own** (2026-10-01, P81). The user, of
   twenty years of music on a NAS: *"i wont let it to my collection of 20 years without precautions
   prepared (no, i do not have a full backup of the nas)"*. Three layers, cheapest first.
   **A snapshot**: one line per audio file — where it is, how big, when it was last written, the tags
   a pass here could overwrite (verbatim, so they go back), a fingerprint of every key it could not
   (their `comment`, their replaygain, their cover: nothing here writes those, so a restore has
   nothing to undo about them, but a pass that *lost* one is found out and named), and a digest of
   the audio packets. A few hundred bytes a track; eleven thousand of them are a couple of megabytes.
   **A safe write**: nothing is written *into* an audio file. A copy beside it is written, the
   recording is proved unchanged — the packets first, which is cheap and conclusive where it says
   yes, then the decoded identity where it says no, because a tag block at the end of an mp3 is
   handed over as audio — and an atomic replace puts it in place. An interruption leaves the file
   that was there.
   **The originals kept**: with a folder named, the first write to a file copies it there whole, and
   **before the rename**, under the name the snapshot wrote down — filing it under noaap's name
   instead was the first thing that broke the restore. That is the only layer that can give a file
   back byte for byte, because a tag round-trip through mutagen is **not** byte-identical even when
   it writes the same values back: measured here, same size, different bytes. What none of it covers
   is said out loud — a disk that fails, a NAS that goes away, somebody deleting the snapshot. It is
   a way back from what noaap did, not a backup.
   **Measured on a copy of the user's own collection — 132 albums, 2000 tracks, 41 GB — and it found
   four faults that no fixture here could have**, because every fixture was Opus and the collection
   is mp3. (1) The copy the careful write writes to lost its suffix, and `tag.kind` reads the format
   off the suffix, so every mp3 was opened as an Ogg and **1662 of 2000 files were renamed and never
   tagged**. Nothing was damaged: the write fails before the replace. (2) The restore gave up on 52
   files whose kept originals were sitting there — a pass that renames a file *and* moves its folder
   defeats both the path and the digest search, and the kept copy needs neither. (3) The digest
   search was gated on an equal size, which a retagged file never has; ungating it uncovered the
   opposite trap, that two files can hold the same recording (a track on an album and on a best-of),
   so it never claims a file the snapshot records under its own name. (4) **The search did not
   scale**: with every file renamed *and* retagged its one guess was gone, so it asked ffmpeg about
   every candidate in the tree for every file — hours for that copy, days for the collection. The
   candidates are ranked now (the folder the last file of this album turned up in, the words the two
   names share, then the size) and only the digest decides, so the ranking cannot be wrong, only
   slow: 54 digests unranked against 24 ranked on a twelve-file fixture. That fixture is in mp3, and
   so are two others, because mutagen's ID3 padding swallows a small retag and an Opus file cannot
   show any of this.
   **The restore of that run found the fifth and the sixth, and they are about the digest itself.**
   (5) The kept originals were filed under the folder the pass had just moved the album into, because
   the relocation happens before the first write — so `restore`, which looks for `kept / <the
   recorded path>`, missed them and put 295 of 2000 files back from their tags instead of byte for
   byte. The copy is filed under the folder the snapshot knows now. (6) **The digest was of the file,
   not of the recording.** `stream_sha` includes the trailing tag block that ffmpeg's mp3 demuxer
   hands over as audio data, so the moment a pass embedded a cover, every recorded digest of an mp3
   was stale: one file was reported *missing* while its audio sat there decoding identically to the
   original, and the check behind "0 files whose audio is not what it was" had been reduced, in
   exactly that case, to asking whether ffmpeg could read the file at all. The snapshot records the
   **decoded** recording now and one function answers both questions — a decode instead of a remux
   per file: three times the CPU, the same read, and over a share the read is the ceiling. A snapshot
   written before this still restores, with the weaker digest it was written with.
   (7) **The resume file was one fixed name beside the snapshot**, so two collections whose snapshots
   share a directory shared one — and the root written into it from the first version was never read
   back. A second copy of the same collection read the first's state, decided all 133 albums were
   done and took in nothing in a tenth of a second, reporting `0 albums` as if that were an answer.
   It is named after its own snapshot now, and the root in it is checked.
   (8) The kept copy filed under the album's recorded folder still dropped the **disc subfolder** a
   file was in — 67 of 2000 — and where two discs held a track of the same name it mapped both onto
   one kept copy, so 3 originals were never kept at all and nothing said so. It is filed under the
   file's own recorded path now, which is unique by construction. (9) The dry run said 1896 audio
   files would be rewritten where the pass then rewrote 2000: the lines were all there and the
   counter looked only for *would be retagged*, missing *would be rewritten with the same tag
   values*, which is a rewrite too — slice 85 again, in the arithmetic rather than the lines.
   (10) **A restore left the owner's own non-audio files in a folder they never made.** The snapshot
   recorded audio only; a pass moves a whole album folder into the scheme, so their `cover.jpg`,
   their `.url` and their thumbnail cache went with it and nothing put them back — 8 files, while
   the album looked restored. The snapshot records **every** file now, with a digest of the bytes
   where it is not audio: 153 such files and 14 MB in the reference collection, which is nothing
   against what it closes.

100. ✅ **The settings are the state; an album may only be excepted** (2026-10-01, P81). The user
   asked twice, and the second time settled it: *"we had consistent state and now we got persisted
   state per album that has to be manually overridden?"* and *"if i do an intake with deliberately
   few options checked to speed things up and decide that i now want to have the image embedded
   everywhere i am not going to hop through 700 albums manually."*
   So: the settings say what every album should have — a cover beside it, a cover in the files, the
   words in the files, looked up at MusicBrainz and LRCLIB, and whether an adopted album is renamed
   into noaap's scheme or retagged at all. A take-in's switches mean *do this now* and are recorded
   as nothing; what the plan records is what was done, as it always did. Every later pass brings the
   albums it touches to the settings, through one `Service._run`, so none can forget. The only thing
   kept per album is an **exception** the user sets in the album view — *keep this album's names*,
   *do not embed here* — which can only take something away, because an album that could ask for
   *more* would be a second state for the library to be in.
   The first case that turned retagging on for an adopted album found a real fault: `run` wrote tags
   with `keep_unknown=False`, which deletes every field the plan does not assert, so a `comment` that
   had been in the file since 2006 went. Until then it could not happen — `keep_tags` meant nothing
   was written at all. An album that came from somebody's folder is retagged the careful way now.

101. ✅ **A whole collection taken in, in one pass** (2026-10-01, P81). `noaap take-in <root>` walks a
   collection artist by artist and leaves every album in the state that was asked for: adopted,
   looked up at MusicBrainz and LRCLIB, a cover beside it and in it, the words beside it and in it,
   the tags written, the names kept or noaap's. Dry by default, and the dry run says **per track**
   what would be renamed and which tag values would change, then the totals per kind — because "two
   albums would be taken in" is not an answer to "how many of my files does this rewrite?".
   **Everything about it is arranged around being interrupted**: the snapshot before the first write,
   the careful write per file, and a state file beside the snapshot that records each album as it is
   finished — so a pass that stops is resumed by the same command and repeats no album. Measured on a
   fixture: stopped after two albums, resumed, the third taken in and the first two untouched.
   `--restore <snapshot>` is the way back, and with the kept originals it is byte for byte.
   **A cover beside an adopted album comes out of its own files.** Nobody published one, so its plan
   holds no address; before this, `cover_beside` was a setting nothing could act on, because only a
   download ever fetched a cover. An adopted album's address is its folder, which its own source
   answers by reading the picture inside the first track that carries one — 269 of 2000 files in the
   reference collection — so a pass can add what the settings ask for without asking anybody
   anything.
   **What it cost, measured, and what it would cost over a share** (30 MB/s, the user's ceiling, for
   11,000 tracks): the dry run 1.63 GB and 8.8 s locally — 5 minutes over the share, against 2.2
   hours before an adoption stopped digesting every file it read (`FolderSource.digests`, which only
   `merge` needs). The pass with everything on and the originals kept: 87 GB read, 88 GB written,
   10 minutes locally — **~9 hours over the share**. The restore: 45 GB read, 38 GB written. Of the
   2093 names present in both the restored copy and the pristine original, 2093 were byte-identical.
   The numbers are what make the three ways to cut the pass worth a decision rather than a shrug, and
   they are recorded with their arithmetic in `docs/spikes/2026-10-take-in-cost.md`, which is the
   measurement's own write-up: method, harness, every phase, and the ten faults in one table.
   **The final figures, on a byte-exact copy and with every fault fixed**: the snapshot 561.6 s and
   43.78 GB read for 2153 files (499 bytes written down each); the dry run 10.3 s and 2.33 GB, saying
   440 renames and 2000 rewrites, which is exactly what the pass then did; the pass 453.1 s, 43.31 GB
   read, 87.80 GB written, keeping all 2000 originals; the restore 211.2 s, 0 missing, 0 changed, 0
   fields of theirs lost — and **2153 of 2153 files byte-identical to the pristine original**
   afterwards. At 30 MB/s for 11,000 tracks that is 2.2 h of snapshot, 6.7 h of pass and 3.2 h of
   asking MusicBrainz and LRCLIB (2 requests an album, ~1 a track, measured with a cold cache): a
   twelve-hour job that resumes per album, or nine hours with the lookups left for a later `repair`.

102. ✅ **A track total is the size of its disc, and a restore puts the folder back** (2026-10-01,
   P81b). What the first pass over a real collection was still getting wrong, after the ten faults of
   slice 99.
   **A track total is the count of the disc the track is on**; the count of discs is its own field.
   All three writers used `len(plan.tracks)` — the whole album — so every track of a three-disc set
   said 29 of 29, and only the mp4 writer ever wrote a disc total at all. In the user's own library:
   **627 files across 23 albums, every one of them wrong, 0 of 627 carrying a disc total.** A
   single-disc album's two numbers are equal, which is why nothing noticed for a year; `signature()`
   reads the same function, so the repair check names all 627 and no single-disc file at all —
   measured over the whole library: 247 albums read, 627 named, 0 collateral. The reader had to be
   fixed with the writers: ID3 keeps each number and its total in one frame, and `TPOS` went through
   the loop that reads a frame's whole text, so a file noaap had just written would have come back
   with `discnumber` = "2/3", be compared against "2", and be reported as needing a retag for ever.
   **A restore means the folder as it was**, so it also removes what the pass created — the plan per
   album, a cover it fetched, the words it saved. It knows which those are because the pass writes
   them down as it goes, by asking what is in the album folder besides its audio before and after:
   measured rather than predicted, so a writer that gains a file does not have to remember to say so.
   Only those paths are removed, never a file nobody wrote down, and the dry run lists every one of
   them first. Before this, the restored copy of the user's collection came back holding **136 files
   its owner never had**.
   **A disc folder the pass empties is the pass's own mess**: bringing a multi-disc album to the
   scheme takes every file out of `CD 1` and `CD 2` and left them standing empty. The pass clears
   them, `rmdir` decides whether they are empty — so a folder holding their scan of the booklet keeps
   both — and a restore makes them again by putting the files back into them.
   **The careful write reads one file instead of two.** The snapshot measured the original minutes
   earlier, so the proof is the copy's digest against that recorded one, and the full comparison runs
   only if they disagree: a stale expectation costs time, never correctness.
   And the two names that were one: a snapshot defaults to `<root>-snapshot.jsonl` rather than one
   fixed name beside the root, so two collections under one parent cannot write to the same file.
   **The hours in slice 101 were too low, and the reason is worth more than the numbers.** They came
   from `/proc/self/io` on this laptop, where the page cache serves the second and third read of a
   file that was just copied — so the pass measured about one pass over the collection where a share
   must carry every read over the wire. Measured directly with the cache evicted, the old
   verification read a file twice (0.97 GB for 0.49 GB of audio) and the new one reads it once, which
   is what the ruling was for; counted per file, a take-in over a 30 MB/s share is 2.1 h of snapshot
   and 8.3 h of pass rather than 6.7, and 10.4 h without the one-read verify. **A local byte counter
   is not a measurement of a network**, and saying so is the correction.
   **And the hole in that slice's own rule, found by the user** (P81c, R-346): writing the count of
   tracks *present* means a disc with tracks 10 and 11 and a gap at 9 is told it has nine of them.
   *"it could have track 10 of 9? that'd be odd"* — so **a track total is never below the highest
   number present on the disc**. `AlbumPlan.disc_length` is the one place that decides it, and all
   three writers ask it: what every file on the disc already agreed on, where that is credible — the
   only thing that can know the length of a disc whose tracks are not all there — and otherwise the
   highest number present, which for a full disc *is* the count.
   One part of that ordering cannot be read literally, and the reason is this program's own history:
   noaap wrote the album's track count into every file, so for all 23 multi-disc albums in the
   reference collection "what every file already said" is the album's length — agreed by every track,
   below no number, and wrong. Taking it would have left all 627 files as they were and made the
   correction a no-op. So where more than one disc exists, a remembered total equal to the album's own
   length is set aside: a value that is the thing being corrected is not evidence about the thing it
   is being corrected to.
   The rule is about a disc, not about a set, so it reaches **single-disc albums with a gap** as well:
   on the reference collection the repair check names 627 files in 23 multi-disc albums (as before)
   and **461 more in 33 single-disc albums**, each of them an album of thirteen tracks whose highest
   number is fourteen. Same defect, one disc at a time.

103. ✅ **A collection on a share comes here a batch at a time** (2026-10-01, P82). The user's music
   is on a Zyxel NAS540, and the first question was whether to put a helper on it. Measured on the box
   (`docs/spikes/2026-10-nas-helper.md`): it reads at 128 MB/s and writes at 72.8, `md5sum` runs at
   25–49 MB/s — but the **decoded** digest, which is what a snapshot records and a careful write
   proves, runs at **0.97 MB/s on mp3**, so 225 GB would take 62 hours there against 2.1 h of reading
   it over a 30 MB/s wire. And `ffmpeg -c copy -metadata` on that box produced a file whose decoded
   digest **differed from the original's** — the mp3 muxer rewrites the Xing/LAME header — so nothing
   on the NAS may write a tag either. A helper would have been worth 1.2 h out of 8.3; copying the
   collection here, taking it in, and copying it back is ~5.2 h with every precaution at full
   strength, because the digests and the tag writes happen where they are fast. **So there is no
   helper.**
   What there is instead is `--staging`, and the shape of it is the user's: *"think about a
   configurable batch size from the start (maybe default to 1/10th of any given running-box's free
   disk space) (careful, dont count mounted nas shares into it like many filemanagers do)"*. A batch
   defaults to a tenth of the free space of **the filesystem the staging folder is on**, from
   `statvfs` of that one path — the trap being that a file manager shows one number for "disk" and a
   batch sized against 5.4 TB of NAS would fill this machine. An album larger than a batch is a batch
   of its own rather than a refusal: a twelve-gigabyte live set is still somebody's album.
   **The share is its own way back**, which is why `--keep-originals` is refused here: every original
   stays untouched until its replacement has been copied back *and* digested. The copy back writes
   beside its target and renames into place — atomic within the share — and **removes nothing until
   every file of the batch is verified**, because an album whose folder the scheme renamed shares no
   path with its old self, so the rule has to be "what the snapshot recorded and the staged copy no
   longer has", applied once at the end. One file that cannot be verified means nothing is removed at
   all. A stop leaves whole batches done or not started; a rerun skips an album that already holds a
   plan, which is what makes the mode idempotent across the renames it does itself — a batch's name
   cannot survive its own albums being moved.
   **Measured on the real share, and it cost two faults to get right.** The one permitted transfer of
   the test data ran at **41.2 MB/s** over Wi-Fi — not the 30 assumed — and the staged round trip of
   2000 tracks took **6036 s**: 93.3 GB received and 44.3 GB sent, peak 9.89 GB staged against a
   10 GB limit, 573 files superseded, 0 unverified. Which extrapolates to **9.2 h** for 11,000
   tracks rather than the ~5.2 h the spike predicted, because the verification reads every file back:
   the round trip carries the collection **three** times, not twice.
   Comparing the share with a local take-in of the same source is what caught both faults.
   (1) **A case-folding share lost the files it had just verified**: `Der W/iii` differs from the
   scheme's `Der W/III` only in case and its files already carried the scheme's names, so the copy
   back wrote them into what the share treats as the same directory, verified all 14, and then
   removed them as superseded — the old path *being* the new path. A superseded path that
   `samefile`s one just written is not superseded, which answers it without knowing how any
   filesystem folds a name. (2) **The owner's own empty folder was tidied away**, because the sweep
   for empty directories ran over the whole root instead of only the folders this pass emptied.
   And a third that was in the measurement rather than the code: a reference run given
   `NOAAP_LIBRARY`, which the CLI does not read, left the library as configured so `relocate` never
   ran — 325 files looked misplaced until it was run with the library set to the root.
   **One cost is worth recording for every pass, not just this one**: a dry run over a share reads
   **2.7× the collection** (80.75 GB, 47 minutes), and the dry run is not why. `would_do` reads
   2.3 MB of a 77.4 MB album; the adoption under it reads 212.6 MB, because
   `sources_folder.collection` opens every file four times — `read_tags`, `audio_length`, and twice
   inside `audio_quality` — and a share re-reads on each open. Opening once would cut every adoption
   there is.

104. ✅ **A restore gives the picture back too, and removing folders is a setting** (2026-10-01,
   P82b). Two defects the reviewer's own run found, both after a restore that reported success.
   **The embedded cover stayed.** A snapshot records the text tags a writer could touch and
   `restore_tags` puts them back; the picture is written by those same writers and was in none of
   their key lists, so a restore left a cover the pass had embedded sitting in somebody's file — 23
   of 36 files in that run, every text tag correctly restored. The snapshot records the picture by
   its digest now, the bytes go once per distinct picture into a folder beside the snapshot, and a
   restore takes a picture out where the file had none and puts the original back where the pass
   replaced one. Without that folder only the first is possible and the second is named in `lost`
   rather than guessed at. `tag.set_picture` is the other half of `embedded_cover`, which had no
   counterpart until now.
   **And the staged mode's record of its own leavings was stamped with the wrong root.** The pass
   writes it against the staging copy, and `read_made` checks the root a record was written for —
   rightly, since another root's record is not this one's — so a restore pointed at the share read
   nothing of it and left every plan the pass had made. It is rewritten in the share's terms when
   the batch is copied back; the paths were always relative and always meant the same on either side.
   **Whether a pass may remove an empty folder that is not of its own making is a setting**, the
   user's: *"maybe we should make clear empty folders a setting?"*, and then *"clearing means
   emptying"* — so `remove_empty_folders`, off by default. Off, a pass clears only what it emptied
   itself, which is what stopped it deleting `Der W/Autonomie`. On, the take-in and the repair also
   take away empty folders under the root they work on, the owner's included, and the dry run names
   each one first. `precautions.empty_under` is the single place that decides it: never the root,
   never outside it, and `rmdir` still has the last word, so a folder holding anything at all stays.

105. ✅ **One open per file, for every question asked of it** (2026-10-01, P83). Slice 103 measured
   the cost and this is the fix. `read_tags`, `audio_length` and `audio_quality` each opened the file
   for themselves — four opens, two of them inside `audio_quality` — and over a share with
   `actimeo=1` every open re-reads, so a dry run of `take-in` over the user's collection read **2.7
   times the whole collection**: 80.75 GB for 41 GB of music, where the dry run's own work is 2.3 MB
   of an album. Locally the page cache hid all of it, which is why it stood for a year.
   `tag.Reading` is one open, seeked back between reads and handed to each of the three, so the
   kernel serves the repeats and nothing is read that was not asked for. mutagen answers identically
   from a handle as from a path — checked for mp3, flac, opus and m4a, for `info`, for the easy tags,
   and for the `Candidate` an adoption records. Every signature keeps working: each function takes an
   optional reading and opens its own when it is not given one, so only `sources_folder.collection`
   — the one place that reads every file of an album — had to change.
   **Measured over the share, adopting all 132 albums: 80.75 GB and 2859 s became 21.5 GB and
   755 s** — 3.8 times less read and 3.8 times faster. It is not down to one read of the collection
   (21.5 GB is about half of it) because mutagen scans an mp3's frames to answer for its length, and
   that is a real read; it is down to doing that once instead of four times.
   **Locally it changes almost nothing, and the measurement had to be repeated to say so honestly.**
   The first pair read 2.06 GB before and 2.66 GB after, which looked like a small regression; run
   again with a warmer cache both fell to 1.3–1.5 GB with the new one slightly *lower*, and the only
   figure that held across runs was the wall time: 10.6 s before, 9.9 s after. A local device-byte
   count varies with whatever the page cache happens to hold, which is the lesson of slice 103 over
   again — it is not a measurement of a network, and here it is barely a measurement at all.

106. ✅ **The original is moved aside, not deleted — and the filesystem is asked what a name is**
   (2026-10-01, P84). The user asked whether everything possible had been done to protect thirty
   years of collecting, and the answer is a gate: **three consecutive clean rounds** over the share
   before the real collection is touched, a round counting only with zero findings of any kind.
   Round 1 found three things.
   **`Path.samefile` is False for the same file on CIFS.** The guard of slice 103 — a superseded path
   that is the same file as one just written is not superseded — compared inodes, and this mount hands
   out **a different inode for each spelling of one file** (measured: 130595 against 130597). So it
   answered "a different file" every time and all fourteen files of an album were deleted after being
   verified, exactly as before the guard existed. The case written for it passed because it used a
   symlink on ext4, where the inode really is shared: **a fixture that agreed with the assumption
   instead of with the filesystem.** So the filesystem is asked — `folds_case` writes one tiny file
   and looks for it under another spelling — and `same_name` compares accordingly, with the inode
   check left as a second line for a filesystem whose inodes mean something.
   **A restore removed an empty folder of the owner's**: it cleared every empty directory under a
   folder the pass had made *and* counted that folder's parents as the pass's, reaching
   `Der W/Autonomie`, empty in their collection and recorded by nobody. Only a recorded folder's own
   subdirectories are cleared now; a parent is merely offered to `rmdir`.
   **And a restore could not give the bytes back at all.** Forty files came back with identical tags
   and identical pictures and different bytes, because a tag round-trip through mutagen is not
   byte-identical and a staged run kept nothing else: the share's own file was the way back only
   until its replacement was verified and it was deleted. So it is **moved**, into
   `noaap-originals/<recorded path>` beside the collection — a rename within the share, which costs
   **nothing over the network** — and `restore(kept=…)` already knew how to copy bytes back from such
   a store. The dry run says how much it would hold, the run says where it is, and it stays until the
   user removes it. That is what makes a staged restore byte for byte, and it is the difference
   between a way back and a good intention. **It is named after the collection** —
   `noaap-originals/<root name>/<recorded path>` — because one store beside the parent would serve
   every collection under it, and two collections can hold the same artist and album: one would
   quietly keep the other's file instead of its own and a restore would hand back the wrong bytes.
   That is the third time one fixed name beside a root has had to be made particular, after the
   snapshot and the resume file.

107. ✅ **A restore of one batch does not reach into another** (2026-10-02, P84 round 2). The rule
   "never claim a file this snapshot records under its own name" protects a track that is also on a
   best-of, and it protects it **within one snapshot**. A batch's snapshot does not record the other
   batches. So restoring batch one went looking for its file by the recording it holds, found a file
   of batch two's album that happened to hold the same recording, and renamed somebody else's file
   away — and then overwrote it from the store of originals, so the collection was left with batch
   two's album missing a track and batch one's track sitting under a name nobody had asked for.
   A restore is told how far it may look: the folders the snapshot recorded a file in, and the
   folders the pass made for them, and nothing else. Empty means anywhere, which is right for a root
   taken in all at once. Verified against the real batch that found it: its territory does not
   contain the album whose file was claimed.
   **The case for it had to be built to reproduce**, which is worth recording. Two albums holding the
   same recording in two batches is not enough, because the batch's *own* renamed copy ranks first in
   the search and is found before the reach ever happens; on the share that copy was missing, which
   is what exposed the other. A fixture that cannot fail is not a case, and the first version of this
   one passed with the fence removed.

108. ✅ **A number the file already carries is kept, and a gap stays a gap** (2026-10-02, P84).
   `3 Doors Down/Greatest Hits` in the user's collection is 01/16 … 09/16, 11/16 … 16/16: fifteen
   files of a sixteen-track album, with no track 10. The pass wrote it out as 1…15 of 15. The
   provider had read every number — it sorts the folder by them — and then dropped them: `Entry`
   had nowhere to put one, so `build_plan` counted by position. That renamed six files and wrote a
   wrong position into each one, and erased the only evidence that anything was missing. The total
   was right (slice 102 keeps 16) while the positions under it were not, which is the worse half:
   the files agreed with each other and lied together.
   So `Entry.number` carries what the source states, `None` where it cannot know — every source that
   fetches before there is a file. `plan.stated_numbers` keeps a stated number and counts only the
   files without one, filling numbers free on that disc, lowest first; of two files claiming one
   number the first in collection order keeps it. An unnumbered folder is still 1…N.
   **And a number now changes only by the user's own reorder** (R-373). `repair` closed the gap after
   dropping a duplicate entry, `delete_track` closed it after a deletion, and `recycle`'s restore
   renumbered a whole disc rather than reuse the number it had. On an adopted rip each of those is
   the same defect by another route; on a fetched album the gap is the truth of what happened, and
   the editor's reorder is one drag away. `plan.renumber` has no callers left and is gone.
   Measured over the user's real library, read-only: 41 gapped discs (the 8 multi-disc and 33
   single-disc of slice 102), none of them in an album holding a duplicate id — so `repair` would not
   have reached them today, and the defect was reaching them through adoption only.
   **Then the same rule one update later** (R-375). Adoption kept the numbers and `prune` and
   `merge_plans` took them away again: `prune` called `arrange`, which counts every disc off from 1,
   and a merge whose track count had changed counted by position. So the authority is named once —
   `plan.states_numbers`, which is "the source knows a track's number without fetching anything",
   and today that is the folder provider. Where it holds, a file that left leaves a gap, a file that
   arrived takes the number its own file states, and a file stating none takes the lowest number free
   on its disc (`plan.keep_stated_numbers`). Where it does not — every source that must fetch before
   there is a file — position follows the source exactly as before.

109. ✅ **A batch is known by what it holds, and only its own marker says it is done**
   (2026-10-02, P84, the gate). The first interruption scenario found it: a SIGTERM after a batch's
   copy back and before anything recorded the batch as finished. The resume then read the *share* —
   a plan file beside the album — as "already taken in", walked past that batch, numbered its own
   batches from 1, and wrote `batch-1-<root>-snapshot.jsonl` over the only record of the twelve
   files the interrupted pass had rewritten. The restore put three batches back, **reported clean**,
   and left the fourth album rewritten on the share. The bytes were never lost — the store of
   originals held every one of them, byte-identical — but no snapshot named them, and `restore` only
   looks in the store for a file a snapshot records. A way back that nothing can find is not one.
   Four things, which only hold together as a set:
   - **the snapshot's name is the batch's content**: `batch-<digest of its album folders>-<the first
     one, for a person to read>-snapshot.jsonl`. A resume cannot collide with another batch's name.
   - **an existing snapshot is authoritative and is never re-recorded.** On a resume the share may
     already hold the files an interrupted copy back replaced, so the staging copy is of *those*, and
     recording them would call them the originals.
   - **"done" is a marker the pass writes, after the copy back is verified** — one record per batch
     in `noaap-staged.json`, written first as not-done with its snapshot's name. What is on the share
     cannot answer the question: a plan file says a pass got that far, not that its batch came back.
     The plan file still means "an earlier pass took this album in", but it may no longer hide a
     folder that an unfinished batch is owed.
   - **the record of what the pass made is written before the copy back, not after it.** Between the
     two is the other window the same SIGTERM opens: the restore put the owner's originals back from
     the store and left the pass's plan and its renamed folder standing beside them. Found by the
     case, not by reasoning.
   And the restore reads the index: `--restore <the staging folder>` puts **every** batch back,
   oldest record last so that the oldest record of a file has the last word, and then reads the store
   of originals and names any file in it that no snapshot of the pass knows. A restore that finds one
   does not report clean. The store is the last net; the restore has to know when it is the only one.

110. ✅ **What a stopped pass leaves, and one pass at a time** (2026-10-02, P84, the gate). Four
   faults, all found by judging the share **as the pass leaves it** rather than only after the
   restore — the restore repaired every one of them before anything looked:
   - **a file only in the store.** A pass stopped between a file's move-aside and its replacement
     leaves it there and nowhere else; the resume takes the batch from what the share holds now, so
     the album stays a track short until somebody restores — and a resume is what somebody does
     instead of restoring. Arithmetic from the share: the first pass copied out 0.077 GB of an
     album, the resume 0.071, the difference one 6.3 MB track. A re-run now takes every recorded
     file the share has lost back from the store, and any run, dry or not, says which files of an
     unfinished batch are only in there.
   - **its own earlier writes read as somebody else's.** The guard that refuses to overwrite a file
     changed since the snapshot compares against the pristine record, so the resume named three
     files it had written itself and refused to touch them. The copy back now records what it
     verified, file by file, beside the snapshot.
   - **a piece of a file wearing the in-flight name.** A signal does not run a `finally`. It stayed
     through the resume, through the restore, to the end. A batch now sweeps those names in its own
     folders **on both sides** — the first version cleaned only the share, and the copy out had
     already taken the piece into the staged copy, so the copy back wrote it out again — and the
     restore sweeps them inside each snapshot's territory.
   - **nothing stopped two passes on one collection.** Two staging folders never see each other's
     index, so the lock lives in the collection's own store: host, pid, start time. A second pass is
     refused by name; a lock whose process is gone **on this host** is taken over, one from another
     host is refused with the file to remove by hand, because a pid elsewhere means nothing here. A
     dry run takes none; a restore takes none and refuses under a live one.

111. ✅ **What a collection's own values are, and what a dry run owes its owner** (2026-10-02, P87).
   The first dry run over the real collection — 1,310 albums, 16,559 tracks, 2 h 52 min — reported
   what the pass would do and, read against the tree, what it would not say:
   - **two albums are never filed under one name.** Three sibling folders of a box set state one
     album between them; the scheme gives all three the same name, so the first to move would own
     it and the others would stay. The whole set is named and left as it is, before anything moves.
     The same inside one folder: `rename` does not overwrite, so two tracks wanting one name left a
     plan pointing at a file that was never written.
   - **nothing with audio in it is passed over in silence.** 1,318 folders held audio and 1,310
     were planned for; the other eight were simply absent from the output. Four were defects in
     what counts as an album — a name beginning with an ellipsis is a name, `1-3` is disc one of
     three, an empty disc folder is not a reason to refuse a box, an album one level too deep is
     still somebody's — and the rest are the owner's own arrangements, now named with a reason and
     a file count in a section both the dry run and the apply end with.
   - **the names a take-in gives are the library's setting.** `--names` defaulted to `scheme` while
     `rename_adopted` defaults to off and the page says so; a plain take-in would have renamed
     2,847 files. The flag overrides, its absence asks the setting, and the run says which and why.
   - **an adopted album's values are its owner's.** Title tidying is about video titles. Applied to
     a folder's tags it took `(Live in Dresden)` out of 108 titles on live albums, left 7 as
     `Lá í mbealtaine ( - Bonus Track)`, replaced `The Metropole Orchestra` with `Within
     Temptation`, and would have written `2003` over `2003-01-01` in 336 files. Now noaap trims
     whitespace, reads numbers its own way, sets the totals and fills what is **empty** — and
     `tidy_adopted_tags`, off by default, is how a library asks for the rest.
   - **a tag can be shorter than the name beside it.** 138 files state exactly thirty characters
     because that is an ID3v1 field, and 69 more carry the same stub in an ID3v2.4 frame; the file
     name spells the song out. The length is what says it was cut, not the version, and what is
     taken from the name is **only the part beyond the cut**: a file name cannot hold `/`, `?` or
     `:`, so taking it whole would write the scheme's substitutes over what the owner typed —
     `Die Brut (Columbiahalle/Berlin` is not `…halle-Berlin`. Where a name really is too long, the
     album artist goes first and the album second, because the folder says both; the title keeps
     forty characters and an ellipsis.
   - **one album artist per album is the album's.** A track whose own `artist` is somebody else
     keeps it — `The Metropole Orchestra` plays on Black Symphony and stays there — while the
     file's `albumartist` becomes the album's, because that is what the field is for.
   - **a file that is already right is not rewritten.** 455 files would have been copied, replaced
     and verified to end up as they were, because the *plan* had no record of their tags. The
     question is asked of the file now, and a leading zero is not an answer that costs a rewrite.
   - **one part of a collection, and never by pointing at it.** The first apply on somebody's
     twenty years is one artist, which `--only <path>` selects while the root and the library stay
     the collection — so the names are the ones the whole pass would give. Pointing the pass at the
     artist folder instead has no right answer and was measured to have two wrong ones: the staged
     apply filed the artist inside itself while its dry run said nothing would move, and the plain
     pass promised a move its apply never made. A renaming pass below its library is now refused
     before it plans.
   - **a number that repeats is kept, reported, and asked about.** A folder stating 1–9 twice is
     the owner's, not a mistake to fix: R-373's rule (of two entries claiming one number the second
     counts as unnumbered) is for a source that cannot know a number, and applying it to a folder
     wrote a position into eighteen of the user's files. The user's order of preference —
     "prompting while working is the last choice; a report afterwards is the first; a pre-run that
     takes half of them out in advance is better; never drop tracks" — is the whole design: keep
     what is stated, end with a **needs a look** section (with a hint where every title of one run
     says "mix"), and where MusicBrainz is asked, let one lookup per such album assign the discs
     when every file falls on exactly one disc and position. Nothing else of the owner's is touched.
   - **a dry run reads the files once and digests none of them.** 87% of the first run was `ffmpeg`
     computing a packet digest an adoption never uses, because `_as_if` built a source of its own
     and lost the flag that switches it off; the collection was also listed three times over. Same
     output, 6.1× faster on the artist it was measured on.


112. ✅ **The last 128 bytes of an mp3 are not the recording** (2026-10-03, P92). The pass over the
   whole collection stopped an hour in, in a traceback, on
   `Earth, Wind & Fire - Earth, Wind & Fire - 04 - Fan The Fire.mp3`: the careful write refused
   because what it had written was not the same recording. It was right by its own measure and wrong
   about the audio, and five batches never ran.
   That file, 10,123,878 bytes, ends in a 128-byte ID3v1 `TAG` block. Change nothing but those 128
   bytes and the decoded PCM differs from **23.186 ms before the end** — one mp3 frame — while
   staying the same length: its final frame header declares more bytes than there are before the
   block, so ffmpeg's demuxer hands the block to the **decoder**. mutagen's `save` rewrites an
   existing block from the v2 frames by default, which NUL-pads what was space-padded. So a digest
   that exists to be invariant under a retag moved under one. A freshly encoded tone does not do
   this, which is why the case builds the shape on purpose rather than hoping for it.
   Three answers, one per ruling:
   - **An mp3 this program writes ends without a tail** (`id3.save(path, v1=0)`). The block itself
     is 30 bytes of latin-1 per field of what ID3v2 holds in any length and any encoding. The first
     version of this carried its comment inwards, because `ID3(path)` reads a trailing block into v2
     frames on load and `save` writes them: the `ripped by Sir_Mc_Tod` on 153 of the 187 Crematory
     originals is in all 153 treated files on the share as `COMM:ID3v1 Comment`, measured — a frame
     their owner never put there. Slice 113 is what the user decided about that.
   - **Neither digest ever sees one.** `decoded_sha` and `stream_sha` go through `ffmpeg_audio`,
     which pipes an mp3 in and leaves a trailing block out of what it feeds. **And an mp3 is always
     piped, tail or no tail**, because the two routes disagree: over a pipe ffmpeg applies no gapless
     trimming, so a one-second tone read from its file decodes to 88,200 bytes of PCM and the same
     bytes piped in give 89,950. Piping while the tail is there and reading once it is gone would be
     the same bug in other clothes — it would refuse every correct write to a file with a LAME
     header. Nothing else is ever piped: opus, flac and m4a give the same bytes either way (measured
     on the user's library) and an mp4's index is at its end, where a pipe cannot reach back to it.
   - **A refused write leaves one file, never the pass.** `Unsafe` is caught per track: the file on
     disk is already untouched — that is what the precaution is for — so the track records why, the
     plan is saved, and the pass goes on to end with a **left untouched** section naming each file
     and its reason. Ending a six-batch run that had copied 47 GB in a traceback was never the right
     answer to one file.
   **And the files already written that way are reached.** 175 of the 187 mp3s of the first batch
   taken in carry the block 1.31.2 wrote. Nothing in the program could see them: the plan says every
   track is done, and the tags inside those files are exactly what the plan asked for, so the
   signature agrees and `repair` skipped the whole album before it ever reached the writer. So the
   one question that tells them apart — `ends_with_an_id3v1_tail`, 128 bytes, mp3 only — is asked in
   all four places that would otherwise skip: the retag gate, `_already_right`, `would_do` (so the
   dry run says `would be rewritten without its ID3v1 tail`) and `repair`'s album-level skip, via
   `tails_to_drop`. **It is asked only where this program wrote the file's tags** — `tagged` is set
   only then — so a library that was told to leave adopted albums alone keeps the owner's own tail,
   128 bytes they have had since 2003, while the block noaap put there comes back off.

113. ✅ **The comment is the one field a write takes away** (2026-10-03, P92b). Slice 112 ended with
   a question rather than an answer: the tail goes, and what about the 30 characters in it? Measured
   on the first batch staged out of the collection — 3,972 files, read-only, one tag read each:
   2,993 carry a comment (mp3 2,779 of 2,937; flac 202 of 610; opus 12 of 425), and of 41 distinct
   texts one accounts for 4,873 values — `ripped by Sir_Mc_Tod` — with `www.NewAlbumReleases.net`
   48 times and `Encoded by EasyTAG` 12. The user, shown that: *"source descriptions I don't want to
   carry on"*.
   - **`drop_comments` is a setting, off by default**, in `OPERATIONS` with the rest of the state the
     library is in, and on the settings page with them. On, every pass that writes a file takes the
     comment out of it — ID3v2 `COMM` whatever its description, Vorbis `COMMENT` and `DESCRIPTION`,
     MP4 `©cmt` — and the check counts them first (`N comment(s) would be dropped`). It is the only
     field a write removes rather than leaves; everything else this program does not model is still
     untouched, which is what slice 53 is for.
   - **It is the library's state, not a flag of one run.** A take-in's switches say what to do
     tonight (slice 100) and are recorded nowhere, so `take_in` reads this one from the settings
     instead of from its `Choices`. And it applies only where the file is written at all: an adopted
     album the library does not retag is not opened to take one field out of it.
   - **Like the tail, it is invisible to the signature** — the plan never asserted a comment, so a
     file full of somebody else's `ripped by` looks done. `comments_in` (one tag read, and 0 for
     anything it cannot read) is asked in the retag gate, in `_already_right`, in `would_do` and in
     `repair`'s album-level skip through `comments_to_drop`, exactly as `ends_with_an_id3v1_tail` is.
   - **It is therefore in the record, and a restore gives it back.** `COMM` and the Vorbis
     `COMMENT`/`DESCRIPTION` were keys nobody here wrote, so the snapshot kept a *fingerprint* of
     each under `tags_outside_ours` and had no value to put back; a restore after a drop would have
     reported every file's comment as lost and been right. They are in `WRITES` now, recorded
     verbatim — `COMM` **per description and language**, because a file may hold `COMM::XXX` beside
     `COMM:iTunNORM:eng` and a record of the first is a restore that silently drops the second.
     And because every record taken before this is silent about the comment, silence is not read as
     "there was none": an absent key leaves the file's comment alone, and a record that really saw
     none says so with an empty list.
   - **And nothing of the tail is moved inwards, whatever the setting says.** `ID3(path)` turns a
     trailing block into v2 frames on load and `save` writes them, which is how 175 files on the
     share came to hold a `COMM:ID3v1 Comment` their owner never put there. The read is now
     `load_v1=False`, so the block is gone and no part of it is written anywhere. `©cmt` also holds
     an m4a's `source`, so the drop happens before the plan's keys go in and noaap's own value
     survives it.

114. ✅ **A record is one line, and only a newline ends it** (2026-10-03, P92c). The whole-collection
   pass stopped an hour into batch 4 — exit 1, a traceback — reading back the snapshot it had just
   written, before it had touched the share: `line 1475: Unterminated string`. `precautions.take`
   writes one record per `\n` and nothing else; `read` split the file with `str.splitlines()`, which
   also breaks on `\x0b \x0c \x1c \x1d \x1e \x85`, U+2028 and U+2029. One file of the user's,
   `Samsas Traum/Heiliges Herz/2/… 01 - Intro.mp3`, carries Windows-Media
   `PRIV:WM/WMCollectionID:…` frames whose descriptions are raw binary, one byte of which is
   **U+0085** — and a tag key is a key in the `others` map, so it goes into the line. Measured on
   that snapshot: 2957 records by `"\n"`, 2980 pieces by `splitlines()`.
   Both readers of a `.jsonl` this program writes now split on `"\n"`. `staged.read_wrote` had the
   same defect and the worse failure: it skips a line it cannot parse **in silence**, so it would
   have forgotten a file it had already copied back and verified, and the copy back would have done
   it again. (`"".split("\n")` is `[""]` rather than `[]`, so the empty-snapshot guard asks about
   the first line instead of the list.)
   All five snapshots on disk read back under the new reader with the count each one recorded:
   Crematory 306, batch 1 4536, batch 2 4053, batch 3 2695, batch 4 2955.

115. ✅ **A folder a rename emptied is this program's own litter** (2026-10-04, P91). After the
   collection came in, **91 empty album folders** stood on the share — every one an album whose
   folder the scheme renamed and whose `.thumb` the pass took away: `Subway to Sally/bastard` beside
   `BASTARD` with its 15 files, `mcmxcv` beside `MCMXCV`. The backup shows 14 files under `bastard`
   before the pass, so the pass emptied that directory and left it standing.
   One line of `empty_under` did it: emptiness was asked of every candidate **before a single one
   was removed**, so deepest-first order decided nothing. `bastard` was asked while its `.thumb` was
   still inside, answered "not empty", and stayed once `.thumb` went. Now a folder whose only
   contents are folders the same list already takes away counts as empty too, and the candidates are
   **every** folder up to the root rather than the immediate parent — a chain is one deep only by
   luck, and `Artist/Album/CD1/.thumb` is four. `rmdir` still decides, so a folder holding anything
   of anybody's stays, and an artist folder with another album in it is not touched.
   **And `repair` names them whatever the setting says.** Removing the owner's empty folders is
   `remove_empty_folders` (slice 104) and stays off by default; but a folder a rename left behind is
   not the owner's, and 91 of them went unmentioned by every pass. So they are always counted and
   named, and the setting decides only whether they also go.
   The case needed the **staged** pass to reproduce: a plain take-in renames the folder in place and
   leaves nothing behind, and the first version of the case passed with the fix removed.

116. ✅ **A folder and the names under it say the same thing** (2026-10-04, P91). `safe_name` strips
   `" ."` from the ends of what it is given, and `track_filename` gave it the whole assembled stem —
   where a part's own leading dots sit in the middle and survive. So `Crematory/...Just Dreaming`
   came out as the folder `Just Dreaming` holding files called
   `Crematory - ...Just Dreaming - 01 - Heaven's Throat.mp3`, and `Fly  (Single)` as the folder
   `Fly (Single)` holding `Crematory - Fly  (Single) - 01 - ….mp3` (the doubled space was already
   collapsed by the whole-stem pass; the dots were not). The **album artist and the album** are
   sanitised before the stem is assembled, because those two are what the folders above the file are
   called. Only the ends go: `Mr. Hurley` keeps its dot, and an album called `Freitag der 13.` now
   loses its trailing dot in the file names as its folder always did.
   **The title and the track artist are not touched**, and the first version of this got that wrong:
   the repair check over the collection showed it renaming two files to take an ellipsis out of a
   *song's* name — `Crematory - Illusions - 11 - ... Just Dreaming.mp3`. A song called
   `... Just Dreaming` is called that; the rule is about the parts that name a folder.

117. ✅ **The room is what is free once a stopped run's copy is out of it** (2026-10-04, P91). A
   batch's size limit is a tenth of what is free, and a batch's identity is what it holds — so
   measuring while a stopped run's staged copy is still on the disk changes the batching and with it
   every key. Measured on the real resume: 499.8 GB free read as 449.8, the cap 50.0 GB as 45.0, six
   batches became seven, and the snapshot of the batch that had stopped could not be reused, because
   no batch held the same albums any more. The run had to be stopped and started again with
   `--batch-size 50G` to get back to the batching the stopped run had used.
   The copy belongs to a run that is over and `_one_batch` deletes it anyway, so it goes **before**
   the room is read. A dry run writes nothing, so there its size is added back instead — the same
   number, and it makes the dry run's plan the one the apply will follow.

118. ✅ **An index entry with no snapshot owes nothing** (2026-10-04, P91). The record of a batch goes
   in **before** the copy out, saying it is not done (slice 109); the snapshot is written after it,
   once the copy is here. A run killed in between leaves an entry with no snapshot — and nothing on
   the share, because nothing is written back until the snapshot exists.
   Reading such an entry as owing is what made a resume take **301 finished albums** out of the
   share again: `owed` covered them, and a folder that is owed is never skipped however plainly its
   plan file says it was taken in (slice 109's rule, which is right for a batch that *did* get that
   far). The resume reported 830 plan files on the share and skipped 529. So an entry whose snapshot
   is not there is dropped from the index with a line saying nothing of it reached the collection,
   and `_owed_folders` ignores it — which is what keeps a dry run's plan the same as the apply's,
   since a dry run writes nothing and drops nothing.

119. ✅ **A staged apply ends where a plain one does** (2026-10-04, P91). The pass over the user's
   collection took in **159 of 160** albums in one batch and said nothing at all about the one. The
   reason: `_the_batch` hands `intake.take_in` a log that goes nowhere — rightly, since its lines are
   about the staging copy — so every per-album refusal reason was discarded, and `Staged.adopted`,
   `refused`, `renamed` and `retagged` were fields nobody added to. 1311 albums in, and the only way
   to learn which folder was refused and for what was to run `adopt.examine` over the share by hand:
   `Spotify`, 23 loose tracks of 18 artists, `18 different albums by their own tags`.
   So `Progress.why_refused` carries the reasons out of `take_in` whether anything read its log or
   not; each batch logs its own refusals in the share's terms and adds its totals to the run's; and
   the run ends with the sections a plain pass ends with — **refused**, with a line per folder, and
   **not taken in**, read against the share after the pass so a folder it renamed or emptied is not
   reported as left behind. Slice 111's promise that nothing with audio in it is passed over in
   silence was made by the plain pass only; the staged one was told `say_leftovers=False` and never
   said it afterwards either.

120. ✅ **A folder no pass will adopt is not copied out** (2026-10-04, P91). A refusal was found on
   the staging copy — which is *after* the folder has been read over the wire, written to this disk,
   recorded in a snapshot and written back. So `Spotify`, 23 loose tracks of 18 artists that nothing
   here will ever adopt, made the whole round trip and came home with every file's mtime new and a
   copy of itself in the store of originals. The user's word for that folder was that it is not to
   be touched. Nothing of its content changed — sha256 proved that afterwards — but a byte-identical
   file written back is still a write.
   `would_refuse` asks the share before the batches are made: one `adopt.examine` per folder, and
   only for folders with no plan file of their own, since an album already taken in is skipped before
   this anyway. The dry run has always paid that cost. A refused folder is named with its reason, is
   not in any batch, and the run's `refused` total counts it.

121. ✅ **Windows Media's library ids go on the comment's terms** (2026-10-04, P91). `PRIV` frames
   whose owner begins `WM/` — `WMCollectionID`, `WMContentID`, `MediaClassPrimaryID` — are a media
   player's own ids, written into the file by something that is not this program and meaning nothing
   outside it. One of them is also what stopped the collection's pass: its *description* is raw
   binary and held a `\x85`, which cut a snapshot record in two (slice 114). The user, asked:
   *"i would, but not necessarily other users"*.
   So `drop_wm_frames`, off by default, with everything `drop_comments` has (slice 113): in
   `OPERATIONS` and on the settings page; read from the library's state rather than a take-in's
   flags; applied only where the file is written at all; counted by the dry run
   (`N Windows Media frame(s) would be dropped`); asked in the retag gate, `_already_right`,
   `would_do` and `repair`'s album skip through `wm_frames_to_drop`. Only mp3 has them, and a `PRIV`
   of somebody else's — `PeakValue` — is a tag like any other and stays.
   **And the record keeps them**, owner and bytes per frame, so a restore puts them back; their
   descriptions never reach a key. `getall` hands frames back in hash order, so the record and the
   comparison are both sorted — otherwise a restore rewrites a file it has nothing to change in.
   An older record is silent about `PRIV`, and silence is not "there were none": an absent key leaves
   the file's frames alone, and an empty list is how a record says it really saw none.

122. ✅ **A hidden folder is the filesystem's, not the library's** (2026-10-04, P91). The repair check
   over the collection, with `remove_empty_folders` on, offered these for removal:
   `.Trash-1000/expunged`, `.Trash-1000/info`, `.Trash-1000/files`, `.Trash-1000` — the NAS's own
   trash at the root of the collection, the one folder the backup deliberately excluded. Its three
   subfolders happened to be empty, and slice 115's rule (a folder whose only contents are folders
   the same list takes away is empty too) then collapsed the whole chain. Nothing applied; the check
   is what caught it, which is what a check is for.
   So the sweep — and only the sweep, `everything=True` — skips any folder with a hidden component
   in its path: one dot hides, two do not (`...Just Dreaming` is an album). A folder that would be
   empty only once something hidden went is therefore not empty either, because what is skipped is
   never on the list that decides it.
   **A `.thumb` inside an album still goes**, by the one route that is this program's own: `only`,
   what the pass itself emptied. That is slice 115's case — a renamed album's `.thumb` — and it is
   untouched by this.

123. ✅ **The lookup that settles an album's discs is not the take-in's alone** (2026-10-04, P93).
   `Crematory/Early Years` on the user's share is 18 files numbered 1 to 9 twice, every title of the
   second run saying "mix". Slice 111 gave a take-in one MusicBrainz lookup for such an album, which
   may change one thing: which disc each file is on. But a take-in **skips an album that already
   holds a plan**, so once the album was in, nothing could ask again — the answer existed at
   MusicBrainz and no pass could fetch it. The repair that fixed 32 albums could not touch it.
   `intake.ask_about_the_discs` is now the one place that decides, and `repair` and `update` ask it
   too, **before** the skip that passes a tidy album by — the same shape as `needs_a_recut` and
   `tails_to_drop`. Where every file falls on exactly one disc and position of one release the discs
   are assigned, the album's numbers no longer repeat, the flag the adoption wrote comes off, and the
   totals follow from the real disc lengths (slice 102's `disc_length`, which answered 0 while the
   numbers repeated — R-425). Where the match is partial nothing is assigned, the flag stays, and the
   line carries the release it looked at and what did not match.
   **An assignment leaves the "needs a look" section and is still said.** The first version simply
   dropped the line once the discs were known, which would have made the one change this lookup is
   allowed to make the one change nobody is told about.

124. ✅ **What a repair skips is decided by asking what it would do** (2026-10-04, P94). The repair
   check over the collection reported 25 albums and only **four** held a real change — and three of
   those four had never been applied by any repair: `Mono Inc. — Head Under Water` (13 files to
   rename by slice 116), `Nightwish — Human. :II: Nature.` (17 files and a cover) and
   `Schandmaul — Sinnfonie`, whose 66 files said three discs where the album has four. They surfaced
   by accident, because slice 123's disc lookup happened to un-skip their albums.
   The skip asked eleven questions — misplaced, borrowed, measured, stale, refound, strays, moved
   sources, recuts, tails, comments, Windows Media frames — and not one of them asked whether a
   **file** would be renamed or retagged. So slice 116's renames reached only albums that failed the
   skip for some *other* reason: `Mono Inc. — Temple Of The Torn` was renamed because it still
   carried ID3v1 tails, and `Head Under Water`, which had none, was walked past.
   It now asks `would_do` — the same list the dry run prints and `run` then carries out, so the
   answer cannot drift from the apply — and skips only where it has nothing to say. That subsumes the
   tails, comments and frames questions, which were three conditions guessing at one.
   **Two things the question needs to be right about.** A *partial* disc match changes nothing, so it
   no longer keeps its album: reported, then skipped like any other tidy album — 21 of the
   collection's 25 duplicate-number albums would otherwise have had a full pass on every repair for
   ever. And the one line `would_do` emits that promises nothing — "a cover would be saved beside the
   album **if** a picture can be found in its files", where the album's own files are the only place
   to look — is ignored by the skip (`MAYBE_COVER`), or an album with no cover beside it is never
   skipped again. A cover that really can be had says where from, and that does keep the album.

125. ✅ **A plan says where its album is** (2026-10-04, P95). A staged take-in runs the ordinary pass
   against the **staging copy**, so the folder provider recorded that copy's path: **154 plans** on
   the user's share name `~/noaap-nas-run/staging/batch/<album>` as their `source_id`, `source_url`
   and `cover_url`, and that folder was deleted when the run ended. Nothing was lost — the cover is
   beside the album — but a cover address that cannot be read is something `would_do` reports for
   ever, so **49 albums** could never be skipped again (slice 124), and a source naming a folder
   nobody will see again is simply not true.
   The staged pass now records the share's own folder, which is what the plain pass records, and
   `repair` re-points a folder-sourced plan whose local paths are **gone or a staging folder's**,
   counted as `N plan(s) re-pointed`. Not merely "not the album's own folder": an album taken in from
   a folder somewhere else keeps pointing at that folder, and `repair --find-moved` exists to follow
   it when it moves — the first version of this overwrote what that had just found, and its own case
   said so. A staging folder is recognised by the **index** a staged pass keeps beside it, so no name
   is assumed and a folder of the owner's called `batch` is not one. A fetched album's `source_url`
   is a URL and is never touched (`provider` is what says which).
   **It needs the rename to reproduce**, and the first version of both the fix and its case missed
   that. The provider records the folder it read; `intake` then relocates the album into the scheme
   and saves the plan in the *new* folder, and nothing updated the address recorded before the move.
   Where the folder already matched the scheme there was no move and the path came out right — so a
   case built on such an album passed with the fix removed, and a fix that looked for the plan in
   the folder the batch had named found nothing at all. All 154 are albums the scheme renamed
   (`asp` → `ASP` and kin), and the pass now asks of where each plan **is**.

126. ✅ **One spelling per artist, even for an album taken in as it stood** (2026-10-04, P96).
   Measured on the share after the merge: `Die Legende Von Nord` held four albums and
   `Die Legende von Nord` held `Angst im Dunkeln`, whose own tags say `von`. On a case-sensitive
   filesystem those are two artists, two folders and two places to look — and `repair` had moved the
   four and left the fifth, which is what slice 23 exists to prevent.
   The cause: `_apply_spelling` is gated behind `tidy_adopted_tags`, rightly, because reading a
   folder's tags the way a video title is read is what slice 111 forbids. But a **case-only**
   difference is not a reading of anything. Nothing of what the tag says changes, only how it is
   cased, and the album's own spelling still counted as a candidate for the library's decision — so
   the spelling that wins may well be this album's. `one_spelling_per_artist` runs outside the
   setting, reports per album, and the rest of the spelling work stays behind it: `JBO` against
   `J.B.O.` is a different name and not this rule's business. A spelling the user chose is untouched.

127. ✅ **A picture is what its bytes say** (2026-10-04, P96). The repair check named three albums as
   wanting a cover — `Cyndi Lauper/To Memphis With Love Live`, `Marilyn Manson/Dead To The World
   Tour`, `Wolfsheim/Dreaming Apes` — each already holding a `cover.jpg`. The ruling supposed the
   address was being read as unfetched; it was not. The first file is a **BMP** and the other two are
   **GIFs**, and `image_mime` knew jpeg, png and webp only — so noaap saw no cover there at all, said
   one was missing on every pass, and would have written another beside it. The line was true; what
   was wrong was the reading of a picture. **A premise handed down is still a claim**, and this one
   cost nothing only because the check is read-only.
   So gif and bmp are pictures. Three things follow:
   - **A tag gets a jpeg.** `APIC`, the Vorbis picture block and `covr` are read by players that know
     jpeg and png, so `as_jpeg` converts anything else before it goes in and leaves a jpeg or a png
     alone — converting a png would cost quality for nothing.
   - **The file beside the album is called what it is.** `cover.jpg` holding a GIF becomes
     `cover.gif`; the picture is the owner's and is kept byte for byte, only the name is corrected,
     and a `cover.gif` already there is somebody's decision and is never overwritten.
   - **And a cover file that is no picture at all is said out loud, once** — an html consent page
     saved as a cover, a truncated download — and left exactly as it is.

128. ✅ **An album already on this disk is copied, not downloaded again** (2026-10-04, P96).
   `merge --new` took in the albums the library lacks by calling `fetch` on each one's source URL —
   the right answer for an album nobody has, and the wrong one for the user's case: **211 albums,
   12.5 GB**, every one of them a folder on this machine with its plan and its `.lrc` sidecars beside
   it. Fetching them would download what was already there and depend on the videos still being up.
   That is why I-344 proposed copying them by hand and the merge did not do it.
   Now `take_new` copies where the album's folder can be read, under the scheme's name, everything in
   it — audio, plan, sidecars, cover — and the album is a library album the moment it lands; the
   source library is read and nothing else, as a merge promises. Where the folder is gone, `fetch`
   runs, which is what the flag meant before. A target that exists is never written into, because
   slice 53's promise holds for a copy as much as for a fetch: **a deluxe edition never quietly
   grows the album that is already here.**

129. ✅ **A case-only tie goes to the spelling most of the library holds** (2026-10-04, P97).
   Slice 126 let `repair` unify a case-only artist spelling, and the first check of it over the
   library would have changed **55 albums across 6 artists**: `Umbra et Imago` (23 albums) rewritten
   to follow `Umbra Et Imago` (4), `Subway to Sally` (22) to follow `Subway To Sally` (1). Both are
   the artists' own spelling, in the lowercase German particle.
   `spelling_rank` ended in `(len(name), name)` — and two casings of one name are the same length,
   so **plain alphabetical order decided**, where `'E' (0x45) < 'e' (0x65)` makes Title Case win
   every time. The rank had no notion of how many albums held a spelling. It was slice 23's rank and
   it had never been reached on this library, because `_apply_spelling` sits behind
   `tidy_adopted_tags`; slice 126 is what exposed it.
   So the count comes after USER, MusicBrainz and the case penalties, and before the alphabet. On
   the user's library: `Umbra et Imago` and `Subway to Sally` stand, `Die Legende von Nord` (1) still
   follows `Von` (4), `DOMINUM` still wins on MusicBrainz over five albums spelled `Dominum`, and
   `Miracle of Sound` (1 against 1) still falls to the alphabet.
   **And the count is never evidence.** The user: *"such stuff should follow MusicBrainz, not
   numbers"*. A spelling that wins on count alone is marked `by count; MusicBrainz not asked` in the
   plan and named as such in the check, so a later lookup replaces it without argument; every choice
   says its basis — user, MusicBrainz, count or alphabet — because a choice nobody can account for
   is not one a reader can disagree with.

130. ✅ **A folder-sourced track's id, and a move that settles in one pass** (2026-10-05, P98 item 1).
   Slice 125 re-pointed a plan that named a folder nobody will see again; it never asked the same of
   the tracks. A folder provider's ref **is** the source file's path, so it is the track's
   `video_id` — and on the user's share **4,086 tracks in 390 albums** named a file that is not
   there: 2,041 under the deleted staging copy, 1,647 renamed in place by the take-in that adopted
   them, 398 under a folder a later pass moved. (12,531 tracks in 1,063 albums already name their
   own file, which is what the state is supposed to be.)
   What that costs: a re-read of such a folder reports **every track new and every known track
   gone** — `13 new, 13 no longer in the source` for an album whose thirteen files are all present.
   An `update --apply` on that report would rewrite the plan as all-new tracks and mark the thirteen
   as missing. It is `points_elsewhere`'s question asked per track, and the answer is the same one:
   **gone, or under a staged index**, never "not the album's own folder" — an album taken in from a
   folder that is still where the plan says keeps pointing at it. The two halves are gated apart, so
   a right source and a wrong track can be true at once. `video_id` does not travel alone:
   its `Candidate`, a `source_override` and a refusal naming the same path move with it, one
   candidate is kept per ref, and `source_state["ids"]` follows so the cheap skip still recognises
   the source.
   **And the file beside the plan has to be that id's file** (R-485, caught by the check before any
   apply). The first rule compared `video_id` with `album_dir/filename` and never asked whose file
   that is. For **68 tracks in 14 albums** it is not the id's: the owner's mp3 was superseded by a
   YouTube opus, so `source_override` holds a video id, `filename` is the opus, and `video_id` is the
   mp3 — gone, as `adopted_name` records too. The rule pointed the owner's id at a recording from
   somewhere else, and because noaap writes a track's id into its file, the check offered to write
   that claim into 68 of the user's audio files (`youtube_id` differs from character 102 of 105).
   So only where `effective_id == video_id`: 4,018 tracks in 376 albums, and **no audio file is
   rewritten by a re-point at all** — a bookkeeping fix that reaches into somebody's files is not a
   bookkeeping fix. The first case written for this passed with the fix removed, which means it did
   not reproduce it; the one that stands needs the owner's `.mp3` gone, the `.opus` beside it, and an
   override set.
   **And the pass that moves an album re-points it.** The question is asked before the skip, which
   is before `relocate` moves the folder and before `run` renames the files — so `repair --apply`
   over the share moved 15 folders and left 13 plans naming the folder they had just left; a second
   apply fixed those 13 and left the one *it* had moved; a third was needed for that. Asked again
   after the move and the rename, where the album is going to stay, it is the same pass. The take-in
   does the same, because it is the pass that gives every file the scheme's name.

131. ✅ **An album's line prints under that album's header** (2026-10-05, P98 item 2).
   The re-point of slice 125 is decided *before* the skip, because an album nothing else is wrong
   with must still be reached — and the skip is before the `=== artist — album` header. So the line
   came out above its own header and read as the previous album's: in one check of the user's share,
   13 of them, each appearing to say that *another* album's plan named this one's folder
   (`=== DOMINUM — Cannibal Corpses` followed by nothing, and above it a line about
   `'Hey Living People'`), with the very first line having no header above it at all.
   The decision stays where it is; only the sentence moves. **Whatever a pass decides before it
   knows it will speak, it says after it has named the album** — a log a reader has to re-align by
   one line is worse than no log, because it reads as a fact about the wrong album.

132. ✅ **A folder with nothing of ours in it and something of theirs is named, never touched**
   (2026-10-05, P98 item 3). `empty_under` (slices 104, 115) clears what a pass emptied and, with
   `remove_empty_folders`, the owner's empty folders too. Neither reaches a folder that is **not
   empty and holds no album**: when the spelling pass moved four albums out of `Umbra Et Imago` the
   folder stayed, because the owner's `logo.jpg` and a `.thumb` were still in it — and nothing
   removed it and nothing said it was there.
   Seven of them on the user's share, in three shapes: five are a logo left by a spelling move
   (`asp`, `emma`, `Umbra Et Imago`, `J.B.O.`, `Oomph! feat. Lame Immortelle`), `omd` is an artist
   folder whose two album folders hold a `cover.jpg` and no plan, and **`Spotify` holds 33 audio
   files of the user's that no pass has ever taken in**. So the line says how many of the files are
   audio: a stray logo and thirty-three unfiled recordings are not the same news, and a total that
   counts them together would hide the second behind the first.
   Named in the sections, never removed, whatever `remove_empty_folders` says. The files are the
   owner's; a folder that noaap has emptied of its own work is noaap's to report and the owner's to
   decide about.

133. ✅ **A collision is a finding, not a log-file footnote** (2026-10-05, P98 item 4).
   `relocate` never overwrites: where the folder an album wants is already taken it stays where it
   is, and said so with `log.warning` — the module logger, which a pass's own log does not carry. So
   the dry run named the collision outright (`the album folder would move to DOMINUM/Night is
   Calling — but something is already there, so it would stay`) and the apply that hit the same wall
   said nothing a reader of its log would find. The flac rip of `Night is Calling` sat under the old
   artist spelling for two applies because of it.
   `relocate` now takes a `say`, the pass prints the line under the album's header in the same words
   as the dry run, and the run ends with `N album folder(s) could not move: something is already
   there`. The take-in passes one too, because it moves folders as well. **Whatever a check
   promises to tell you, the apply tells you in the same words** — an apply that is quieter than its
   own dry run is an apply nobody can read.

134. ✅ **`repair --only` and `update --only`, the take-in's rule on the library** (2026-10-05,
   P98 item 5). `update` could be narrowed to an artist from the API and not from the command line,
   `repair` not at all — so settling the naming of **two** albums meant a run over 1,311, or a
   one-off script calling the Service by hand. Both now take `--only PATH`, `append`, with
   `intake.under_only`'s matching (R-417, point 1): a path under the library, component by component
   through `text_key`, so one spelling works for `adopt`, `merge`, `take-in`, `repair` and `update`
   alike, and a value that chooses nothing is named instead of silently doing nothing.
   **`--only` says what is touched, never what anything is decided from.** The spellings are settled
   from the whole library before the first rename (slice 129) and the scheme's names are library-wide
   — a run over one album of `Umbra Et Imago` must still learn that twenty-three others spell it
   `Umbra et Imago`. The two whole-library sections at the end (empty folders, artist folders without
   albums) are the other side of that line: asked for one part of the collection, a pass has no
   business naming folders somewhere else, so they are skipped.

135. ✅ **A file the plan already holds is that plan's track, whatever the fresh id says**
   (2026-10-05, P100). A folder's refs **are** paths, so a fresh read calls a file by where it is
   while the plan calls it by `filename` — and where a pass or a person took another candidate for a
   track, those two names are different: the plan's id is the owner's file, which is gone, and the
   file in the folder is the copy that superseded it. `merge_plans` matched on `video_id` alone, so
   it reported **both** — the superseded track as `no longer in the source` and its own replacement
   as new.
   Measured on a copy of the user's albums before any of it could reach the library:
   `DOMINUM — The Dead Don't Die` went from **20 tracks to 37**, numbers `1,1,2,2,…,28`, discs
   {1: 28, 2: 9}, 17 entries marked not in the source, and **37 audio files in a folder that held
   20**. `DOMINUM — Hey Living People` 13 → 21, `Schandmaul — Wie Pech & Schwefel` 15 → 22,
   `Wahre Helden` 13 → 20, `Saltatio Mortis — Manufactum II` 17 → 23: **20 albums of the library**,
   every one of which a whole-library `update` would have doubled.
   So before anything is counted or merged, a fresh folder read is put back into the plan's own
   names: matched on the path **relative to the folder that was read**, so a track in `cd1/` is only
   ever the plan's `cd1/` track, and **never where two tracks could answer** — an ambiguous name is
   left to the id, which is what happened before. A fetched album is untouched by this: its ids are
   video ids and they remain the only answer.
   The lesson is slice 130's again from the other end. **The id is a record of where a track came
   from; the file beside the plan is what the album is.** A pass that confuses the two either loses
   the history or duplicates the music, and this one would have duplicated it.

136. ✅ **An m4a's `source` is not its comment** (2026-10-05, P99). `ID3_KEYS` says why in its own
   margin: `source` is a `TXXX` rather than a `COMM` because "COMM is where people keep their own
   notes, and this must not land on top of one". `MP4_KEYS` put `source` in `\xa9cmt`, which **is**
   MP4's comment atom — so every m4a noaap touched had its owner's note overwritten with a URL, the
   thing the rule exists to prevent.
   And it made `drop_comments` a loop: the drop took the atom away and the writer, three lines
   later, filled it again with noaap's own value. `scifistories1977 — [REVIEW] The Black Ship -
   Chapter 61 Part 3` was therefore offered for the same comment-drop at **every** check, the apply
   said `retagged`, and `repair --only` after it still said `1 comment(s) would be dropped`. An
   album nothing could ever finish with.
   `source` now has a freeform atom of its own, as it has a `TXXX` in ID3 and a field in Vorbis. One
   m4a in the user's library, so the measured reach is small — the rule was wrong all the same, and
   a pass that can never finish an album is worse than the tag it was trying to fix.

137. ✅ **A release a person named is theirs to keep** (2026-10-05, P101).
   `mbid` was the one album field no edit could hold. Every other one goes through `_merge_fields`,
   which honours a user edit; `merged.mbid = fresh.mbid or merged.mbid` took the search's answer
   unconditionally, and **no `mbid` key was ever written into `provenance` anywhere in the
   codebase** — so a hand-set release was gone at the next update and there was no way to say which
   release an album is.
   The user's own case is `DOMINUM — Night is Calling`: a rip of **disc 1 of a 2-CD release**. The
   search can only ever answer with the 13-track single-disc edition, because `release_candidates`
   requires the track count to be within one and `match_release_tracks` requires 80% of the
   *release* to be matched — a release that is half present is exactly what a search is built to
   reject, and rightly so.
   So: `provenance["mbid"] = "user"` is a pin; the merge leaves it alone; `enrich_release` opens
   **that** release and does not search at all (`search_releases` is not called, which the case
   asserts); and for a pinned release the release-side share is dropped while the plan-side share
   stays — **a person who named the release has made no accident**, and 13 files answering for
   13 + 13 is a rip of one disc, not a mismatch. A pin that still does not answer changes nothing
   and says so.
   Set by pasting the MusicBrainz link: the id is taken out of a URL because a link is what a person
   has in hand, a `release-group/…` link is refused (a group has no media and nothing could be
   matched against it), and an empty field hands the choice back to the search. The check says
   `release pinned by you`, so a reader can tell a chosen release from a found one.
   **And the pin has to reach the plan that is actually enriched** (R-490, found by the real run the
   day this shipped). The pin is stored on the album's plan; `fetch` builds a **fresh** plan from the
   source, enriches that, and only then finds the stored one and merges. So `pinned_release` was
   asked of a plan that could never carry a pin: the pass logged *looking for the release*, took the
   search's answer, and the merge then kept the pinned **id** — the plan would have named one release
   while its names, numbers and discs came from another. `carry_the_pin` moves it across before the
   enrichment, and the stored album is looked up once and reused by both branches, because
   `find_plan` reads every plan and over 1,311 albums on a NAS that is not a question to ask twice.
   Every case written for the pin exercised `enrich_release` and `merge_plans` directly, so not one
   of them saw it. **A rule about what a pass does has to be driven through the pass.**

138. ✅ **One spelling per artist, from the MusicBrainz artist entity** (2026-10-05, P102, absorbing
   P97). Slice 129 chose a case-only spelling by how many albums hold it and marked the choice
   `by count; MusicBrainz not asked`, because the user's word is *"such stuff should follow
   MusicBrainz, not numbers"*. This is the lookup it was waiting for.
   **Asked of the artist, never of a release.** A release's artist *credit* is per release and
   carries a sleeve's stylisation; the artist entity has one `name`. Pinning `DOMINUM — Night is
   Calling` to its 2-CD release showed the difference at once: that sleeve says `Dominum`, and the
   update offered to rename thirteen files and undo a spelling five other albums had just been given
   (R-490, I-373). So the credit never decides what a library calls somebody.
   The order is now: a spelling **the user chose**, then the **entity's name**, then — only where the
   entity does not answer — a release credit, the case penalties, the count, the alphabet. One
   request per artist key per pass, cached in the client for thirty days, and **nothing is asked at
   all about an artist whose every album is private**: a lookup sends a name to somebody else's
   server, and for an album a patron paid for the question is what is forbidden, not only the answer
   (slice 72). `repair` settles the whole library and `update`'s `_settle_artist` writes it too, so a
   fetch does not leave an old spelling standing until the next repair.
   **And the basis names the ground that actually decided.** `spelling_basis` said `alphabet` where
   the case penalty had chosen — `DOMINUM` (5 albums) loses to `Dominum` (2) on that ground and on no
   other — so `case` is now one of the answers. A check that names the wrong reason is worse than one
   that names none, because a reader will believe it.
   **Only the artists a run touches are asked about** (R-497). The candidates still come from the
   whole library, because a spelling is a library-wide fact — but the user's library has **153 artist
   keys**, which on a cold cache is 2.8 minutes of rate limiting before the first line is printed,
   and `repair --only` over one album paid all of it. So `--only` narrows what is asked as well as
   what is touched, and a whole-library run still asks for everything.
   The measured result on the user's library: **15 albums across 7 artists**, every one on the
   entity — `Die Legende Von Nord`→`von` (5), `EMMA`→`Emma` (3), `Miracle Of Sound`→`of` (2),
   `Sven Van Thom`→`van` (2), `Amy MacDonald`→`Macdonald`, `Sons Of Seasons`→`of`,
   `Trollfest`→`TrollfesT`. Two of them **reverse** what slice 129's weaker grounds had chosen a day
   earlier (`Von` by count, `Of` by alphabet), which is the rule working. And the entity confirms
   `DOMINUM`, `Umbra et Imago`, `Subway to Sally` and `Van Canto` exactly as they stand, so the four
   albums that caused all this do not move at all.

139. ✅ **One walk, one model: the library's state lives in memory** (2026-10-05, P103).
   Measured on the user's library over NFS — 1,523 albums, 20,100 tracks — **one `/api/state` cost
   107 seconds, cold and warm alike**, because it walked the library three times for one answer:
   `albums()` read and parsed every plan and globbed every album for its cover; `missing()` read all
   1,523 plans *again* and `stat`ed every one of the 20,100 files (62.6 s of the total);
   `library_version()` ran an `rglob` over **every directory in the tree** to hash mtimes the other
   two had already read. `album()` asked for that version too, so one cover request paid the whole
   `rglob` — and a page load fires one per card. The user's page took 8.3 s per poll on a warm local
   disk and a request from the side waited **104 s** behind the polling.
   Now one `glob('*/*/.noaap.json')` — **0.58 s**, because it does not descend into an album — and
   **only the plans whose file changed are parsed again**: 0.01 s each against 7.25 s for all of
   them. The covers are remembered per album folder mtime. Everything the page reads comes out of
   that one model: the rows, the version, the album index. Measured after:
   **first request 23.4 s (the one walk), every one after it 0.00 s, `album()` 0.01 s, a rescan tick
   with nothing changed 0.63 s.**
   **What makes it stale is said plainly.** A pass of ours marks it stale the moment it finishes, so
   the page always sees what it has just done; a change made behind noaap's back is picked up by a
   rescan at most a minute later, and `/api/state` carries `held_at` and `stale_after` so a reader
   can tell. Changing the library root throws the model away rather than marking it stale — every
   album in it belongs to the library just left.
   **The expensive question is asked where somebody asked for it.** One `stat` per track is 62.6 s,
   so "how many files are missing" is measured by the repair check and by a repair, and the page
   reports what the last sweep found **with the time it was taken**. A number nobody can date is
   worse than no number.
   **And nothing that carries bytes waits behind a walk.** The walk's lock is the walker's alone and
   is never held while anything is read; a cover and a track are answered from `held_now()`, which
   never walks even when the model is stale. On the page: one state request at a time — the timer
   fired every 700 ms while a job ran, so against a 107 s answer it queued dozens of identical walks
   with every cover behind them — and a slow answer is *waited for*, with a ceiling far above the
   staleness bound, rather than shown as a server that went down.

140. ✅ **A card gets a thumbnail, not the cover** (2026-10-05, P104).
   Measured on the user's library over NFS, 1,221 covers beside their albums, **before anything was
   changed**: a first view of forty cost 1.07 s, 27 ms each — **not a stall**. What it did cost was
   bytes nobody could see: a card is `10.5rem`, about **168 px**, and it was sent the cover as it
   stands, 65 KiB on average and 220 KiB at worst, 2.67 MB for one view and about **82 MB** for the
   whole grid. The file crossed NFS, crossed the connection, and the browser then scaled it down.
   That measurement is why this slice is small. The page already asked only for the cards in view
   (slice 90's `IntersectionObserver`) and `/api/cover` was already cacheable, so there was nothing
   to fix there — the honest finding was reported as such and the work narrowed to the one thing the
   numbers showed.
   So `?thumb=1` serves a 336 px JPEG — twice the card, so it is still sharp on a dense screen —
   kept on the **local** disk under the cache directory, keyed by the cover's path, **size and
   mtime**: a replaced cover is a new key and the old entry is simply never asked for again, so
   nothing has to be invalidated. The panel and the player ask without it, because they show the
   picture large. A cover already no bigger than a thumbnail is sent as it is rather than re-encoded,
   and **nothing here can fail a request**: a cache that cannot be written or read falls through to
   the cover itself.
   After: **65 KiB → 24 KiB per card**, 2.67 MB → 0.98 MB for a first view, 18 ms → 10 ms warm, and
   the whole grid **82 MB → 30 MB**, for 0.73 MB of cache per forty albums.

141. ✅ **An album with no cover of its own takes its release's artwork** (2026-10-05, P105).
   The user pinned `Apocalyptica/Cult` to release `73fcbc7e…`, the lyrics followed, and no cover
   arrived. **Two reasons, both of them silent:**
   First, `_cover_addresses` began `[plan.cover_url, plan.cover_fallback_url]` and only fell through
   to anything else when **neither** was set. An adopted album's address is its own folder (slice
   101) — which is truthy — so for every album taken in from a folder the list was exactly one
   address: itself. Cult's flacs carry no picture, so every pass said `could not fetch any cover`
   and no other address was ever considered, pin or no pin. Second, the only Cover Art Archive
   address this program ever produced was written by `enrich_release`, and **`repair` never
   enriches** — `enrich` is called from `fetch` alone. The pin was recorded and nothing had asked
   the archive about it since.
   And a third thing, found while fixing those: **the archive is nobody's provider.** `source.art`
   is a provider's own way of getting a picture, and the folder provider reads a path and raises
   `no cover in …` for anything else — so even with the address in the list, handing it to the
   provider could only ever fail. A `coverartarchive.org` address (and the `archive.org` it redirects
   to, and no other host) is fetched directly.
   **The release before its group.** A group's front is whichever edition the archive picked for the
   group: for that pin it redirects to `a1b9ddb1…`, a different edition's artwork for an album
   somebody named by hand. So the release's own front comes first, both in the address list and in
   what `enrich_release` writes, and **behind it stays whatever the album already had**: a source's
   own art is *this* album's picture, which is worth more as a second try than another edition's, so
   the group only fills a fallback slot that would otherwise be empty. What the plan already holds
   still comes first of all, and a cover beside the album is never fetched over at all — that path
   only runs when there is none.

142. ✅ **"Identify with MusicBrainz", and a dry run that says what it would change**
   (2026-10-05, P106). The user pinned a release by hand and nothing happened until a pass was run
   from a terminal: the panel had no way to say *go and look this album up*.
   **And the premise the button was asked for did not hold.** `update --dry-run` on an album already
   in the library returned at the merge — `0 new, 0 no longer in the source` — and said **nothing
   about the album's own fields**: not the title it would rewrite, not the numbers it would move, not
   the discs it would assign. The `NN would be retagged` lines are `repair`'s, a different pass
   asking a different question. So there were no "dry-run lines" to show.
   The dry run already computes the plan it would write, so it now compares that with the one on disk
   and says what it would really do, field by field: `mbid: nothing → 73fcbc7e…`, `03 title: … → …`,
   a track that moved disc or number, a recording id arriving, a cover. **One function, two callers**
   (`changes_from`), so the command line and the button cannot drift.
   The button is a repair's two steps — look, then apply what was listed — because the lookup rewrites
   titles, numbers and discs, and a person who cannot see that first is being asked to trust it
   blind. `identify` is in the read lane and writes nothing; `identify_apply` is the write. Always
   `deep`, because the point of the button is to look: an album the cheap check would call unchanged
   must still be read.
   **What it does not do is as much the point.** A pinned release is opened and not searched for
   (slice 137); a field the user typed is untouched; and for an album adopted from a folder **the
   files' own tags still beat the archive** (`FIRSTHAND`), so a release cased differently brings its
   ids and leaves the name alone — what it said is kept in `auto`, where a reset can reach it. Three
   expectations written for this were wrong in exactly that way before the cases were made to fail
   without the fix: a release whose title differs is not even a candidate, and an adopted album's
   title is deliberately not the archive's to change.

143. ✅ **Where an album came from is the provider's answer, not "YouTube"** (2026-10-05, P107).
   The album panel ended in `<a href=p.source_url>open on YouTube</a>` for **every** album, whatever
   its source. For the **1,312 of the user's 1,523 albums adopted from folders** that is a link to a
   path on the NAS, labelled as YouTube: it opens nothing, and it says something untrue about where
   the music came from. Found in a browser run over the real library, not by the suite — nothing in
   it had ever looked at that line.
   The folder provider had answered this correctly all along: `url_for` returns None, and its own
   margin says why — *"nothing to open in a browser. A file manager would need a `file://`, and
   offering one that half the desktops ignore is worse than offering none."* The page simply never
   asked. So a link appears only where the address really is one a browser can open, it is named
   after the provider that minted it (`open on SoundCloud`, `open on Patreon`), and a folder-sourced
   album says **`taken in from <path>`** as words rather than as a link. No source at all shows
   nothing.
   Slice 51's rule, one level up: **the page must not decide what a ref means.** It held for a
   track's candidates from the day it was written and this one line had been quietly breaking it
   ever since folders became a provider.

144. ✅ **A release with no artwork of its own has a group that may have some** (2026-10-05, P108
   item 1). The user pinned `Apocalyptica/Amplified — A Decade of Reinventing the Cello` to release
   `31a80627`. Measured against the archive: that release's front is a **404**, and its group
   `ff01a595-ff51-395a-9ed1-b2c93f54f659` answers **307** — to `3395850b`, which is another edition
   of the same album. Every pass logged `could not fetch any cover … no cover in <folder>`.
   Slice 141 ruled release-then-group and the group was still never tried, because **the plan did not
   hold the group's id**: only MusicBrainz knows it, and the address list is offline by design. So
   the release group is written into the plan the moment a lookup tells us, and its front is the last
   address tried — after the album's own files and after the release's own front, because a group's
   front is whichever edition the archive chose and that is a worse answer than this release's own
   whenever there is one.
   **Why P105's case did not catch it:** the release it used *has* a front, so the list never ran out
   of addresses. A case for "the archive answers" is not a case for "the archive answers 404 and
   something else is left to try" — and only the second one is a fallback.

## 10. Rules for whoever implements this (lessons from the v2 loop)

- **Fix wrong data where it enters,** not where it shows up. If a number is wrong on a
  card, trace it to the extractor before touching dedup/merge/display code.
- **Every bug gets a fixture test first** (captured JSON, offline), then the fix.
  No ad-hoc `debug_*.py` / `test_*.py` in the repo root, no live-only "tests",
  no inspecting a running server by constructing a second instance in another process.
- **Never tune for one artist.** Two failures in a row on the same symptom → stop and
  re-examine the assumption, don't add a third layer.
- Commit everything that runs (v2's whole frontend was never committed).
- No hardcoded `/home/tordt/…` paths or ports scattered across files; one config.

## 11. Salvage from v1/v2 (as reference, rewritten)

- Filename convention and tag rules (albumartist vs artist, disc only if >1) — v1
  `downloader.py:1282`, `audio_tagger.py:186-244`.
- Per-track tag-on-arrival (v1 `downloader.py:748-785`).
- Release-country priority table (v2 `models.py:120-135`); MB track count = sum of
  `media[].track-count` (v2 `musicbrainz_service.py:316`).
- YouTube id extraction (v2 `youtube_deduplicator.py:22-67`), extended for
  `music.youtube.com` and `OLAK5uy_`.
- Cover Art Archive lookup (v2 `musicbrainz_service.py:358-419`).
- Title-suffix lists from v2 `normalization_service.py`, **without** the year regex.
- Negative keywords for search ranking (karaoke, cover, reaction, …; v1
  `advanced_search.py:678`).

## 12. Decisions (2026-09-22)

- Compilation albumartist = curator; album = playlist title minus curator prefix (§2.1).
- Library root is configurable (§7).
- v3 lives on a **new branch** in this repo, which became `main` (`github.com/smtws/ytalbum`).
- Intro/outro trimming: yes, later — after the rest is stable (slice 8).

### Decisions of 2026-09-26 (the QA run and the backlog that came out of it)

- A lyric is the user's by its **bytes**, not by a flag they set; the record of what we wrote
  decides, and where there is no record, lrclib is asked about the entry we stored (§9, slice 21).
- Deleting your own lyric gives the mark up with it, so `--refetch` can answer again (§9, slice 21).
- The lyrics panel **writes**, and the editor never looks anything up — a save can then never be
  answered by replacing the words just typed (§9, slice 26).
- A rejected lrclib entry is remembered **per track** and never offered for it again, which is what
  a deleted file could not say (§9, slice 27).
- A fetch renames only the album it is fetching; the library's spelling wins and `repair` is what
  upgrades the rest (§9, slice 23).
- `repair` decides each artist's spelling once, before it renames anything (§9, slice 23).
- A length reference lrclib contributes is the **consensus** of its candidates, not the nearest
  one, and the query drops only the instrumental markers (§9, slice 24).
- A single's album name follows its own track's title (§9, slice 25).
- **Repair is reachable from the web UI, behind a confirm rather than a preview** (P10, catalog L):
  a preview would need a pass that reports without writing, which is the fetch preview's job and
  not repair's. This decision lives nowhere else.
- The fetch preview merges with what is in the library, so it shows what a fetch would write rather
  than a fresh reading of YouTube; Shift+click skips it (§9, slice 28).
- Resetting a field drops its provenance rather than guessing it, because `auto` records the derived
  value and never its source (§9, slice 29).
- Opening an album reconciles its lyrics with the disk; the library grid deliberately does not
  (§9, slice 30).
- A number the user **typed** counts in the disc they are putting the track on; one left alone counts
  where the track was (§9, slice 32).
- **No JavaScript test harness inside a feature package** (§9, slice 31): the arithmetic lives in Python
  where it is tested and the browser cases are the evidence for the rest. Whether the repo gets one
  is open — `docs/backlog.md` item 10.

### Decisions of 2026-09-27 (backlog 11 and 12, catalog T)

- **Two edge colours, not one.** `--line` divides two surfaces and stays quiet; `--edge` outlines a
  control and clears the 3:1 WCAG 2.2 1.4.11 asks of it. Raising `--line` itself would have been one
  line of CSS and would have ruled every table on the page, so the cost was paid in a second token
  instead. Which selectors take which is written above the tokens in `style.css`.
- **A boundary is only required where the boundary is what identifies the control.** The delete ✕
  keeps `border-color: transparent` and is identified by its red glyph; the album card is identified
  by its cover and title. Both measured and left alone (catalog T2, T4).
- **The drag handle is found rather than explained.** A stronger glyph and a row-level hover, no
  stored state: a one-time hint that must be dismissed is a nag, and it would be the only piece of
  remembered UI state in the page (backlog 11, catalog T5/T6).

### Decisions of 2026-09-27 (an alternative source per track, §9, slice 34)

- **The playlist video is the identity; only the audio may be pointed elsewhere** (§9, slice 34). Replacing
  `video_id` would have been fewer lines and would have broken `in_source`, prune, the merge and the
  MusicBrainz match, all of which are about *which entry this is*, not about which file plays.
- **A source change clears the trim rather than keeping or converting it.** Marks are seconds of a
  particular recording; the new one has its own silence at the front. Converting them would be a
  guess, keeping them would cut the wrong audio, so they go — and the UI names them before it asks.
- **The uploader follows the audio, not the identity** (§9, slice 34): "trim everything from this channel"
  is about who encoded the file in front of you. It is re-read from the video actually used, in both
  directions, and a merge no longer overwrites it from the playlist entry.
- **No automatic re-timing of a user's lyrics.** The notice states the problem and leaves the fix to
  them; doing it for them is P23, where it can be confirmed and undone.

### Decisions of 2026-09-27 (stamping to the file's clock, §9, slice 35)

- **The tools act on the line the cursor is in, not on a widget per stamp.** The editor is a
  textarea and stays one (the task pins it as the source of truth), so per-stamp buttons would mean
  a second representation of the same text and two ways for them to disagree. The cursor already
  decides the line for the tap; play, nudge and shift use the same rule.
- **The stamp is a tenth.** `audio.currentTime` carries a dozen decimals of nothing, LRC players
  read hundredths, and a tenth is finer than anyone can tap — the same decision §9, slice 31 took for the
  trim marks, for the same reason.
- **A nudge plays what it changed.** Aligning by ear means hearing the result immediately; a nudge
  that only rewrote the text would make the user press play after every tenth.
- **No automatic alignment.** Still true after P23: everything here is the user pointing at a
  moment. Whether a model could place the lines is a separate question, and a measurement rather
  than an opinion — P24.

### Decisions of 2026-09-27 (what the alignment spike is for, P24)

- **Automatic lyric timing is measured, not adopted** (`docs/spikes/2026-09-alignment.md`, backlog
  item 14). Forced alignment reaches a median of 0.94 s per line on 17 of 20 real tracks with 442 MB
  of models and no GPU, and needs two independent aligners compared against each other to be safe.
  Nothing was added to `src/`; the spike's environment lived outside the repository and was removed.
- **If it is ever built, it goes behind a provider boundary, not into the core.** ytalbum must not
  acquire a baseline of "modern GPU plus gigabytes of CUDA and model weights": the default provider
  is `none`, the app is exactly what it is today without one, and local inference is one optional
  implementation beside a self-hosted endpoint and commercial APIs. The spike's §5 carries the
  interface sketch and the costs of each.

### Decisions of 2026-09-27 (the timing boundary, §9, slice 36)

- **Built as the spike recommended, with two providers rather than one** (P25): `local` for whoever
  has the extra, and `http` + `ytalbum timing-serve` because "run the inference on the other
  machine" is this household's actual deployment and is fifty lines around the same code.
- **The provider never writes.** It answers with lines and a `Timed` record of itself; the editor
  shows them and the user saves. That is what keeps §9, slice 21 untouched and makes a wrong alignment cost
  a "Cancel" rather than a restore.
- **`unplaced` is part of the answer**, not an error. A provider that cannot say which lines it
  would not place cannot be trusted with the rest.
- **The words choose the aligner.** The language comes from counting stopwords in the lyrics, as the
  spike did, rather than from a configuration key or a language-detection dependency.
- **No transcription and no library-wide pass in this package.** Both are decisions of their own
  (backlog 15 and 16), and the measurements do not support making them quietly.

### Decisions of 2026-09-27 (paid providers, §9, slice 37)

- **The user decided that audio may leave the house**, having weighed that it came from YouTube in
  the first place. The code's job is to say so every time, not to relitigate it: three notices and a
  confirm, and local providers that never do it.
- **Built without spending anything.** Fixtures are the vendors' documented shapes, the clients were
  exercised against a local server, and a live test exists behind an environment variable for
  whoever has a free-tier key. A package that needs a credit card to be finished would have been the
  wrong shape for this project.
- **Never retry a metered request.** One press, one request, one possible invoice.
- **A transcribe-only vendor must say so.** Deepgram's `capabilities()` is the mechanism that keeps
  the page honest, and it is the reason the boundary has `capabilities()` at all.
- **A draft is labelled everywhere it appears** and is offered only where there is nothing to lose.

### Decisions of 2026-09-27 (a second opinion, §9, slice 38)

- **A cross-check that costs the alignment is worse than no cross-check.** Every failure in the
  second pass degrades to "unchecked" and keeps the first answer. Three real failures proved the
  rule before it was written.
- **The two methods must hear different things.** Running the second model on the same separated
  stem made it useless (11 of 42 lines nowhere) *and* would have made agreement mean less.
- **Two rules, two constants.** Per line: past `timing_verify_threshold` the stamp is not placed. Per
  track: `timing_verify_lost` decides which regime applies. Sharing one number made generosity about
  jitter widen what counted as a salvageable track.
- **A check may not discard the better method.** The whole-track case keeps every primary stamp and
  says loudly that a second method disagreed, because the measurement (catalog Y, five tracks out of
  sixteen) found the primary right every single time the condition fired. The rule that placed nothing
  was written first, tried, and overturned by its own evidence — which is the most useful sentence in
  that section.
- **Refuse rather than place a doubtful line.** A line the two disagree about keeps its words and
  loses its stamp; the user has P23's ▶ and ± for exactly that. It costs some stamps the primary had
  right — measured in catalog Y — and that is the price of not needing a human on every line.
- **Disagreement about most of a track places nothing.** The failure mode that occurs is half a song
  out; a half-filled editor would look like success.
- **Size decides where something lives.** 3 GB is its own extra, off unless installed, and the
  README says what it downloads before anyone types the command.

### Decisions of 2026-09-27 (the editor's own clock, §9, slice 39)

- **Two views must not be fed by two truths.** While the editor is open there is exactly one truth
  for playback, and it is the textarea. The file is the truth again the moment the editor closes.
- **One door for every rewrite.** Assigning `.value` fires no event; a redraw that listens for one
  therefore has to be told. `editorText()` exists so that no tool can quietly stop being seen.
- **A second copy of the words needs a name.** Found by looking at a screenshot, which is where the
  page's unlabelled things are always found.

### Decisions of 2026-09-27 (two slots, §9, slice 40)

- **One setting per decision, not per subsystem.** Aligning and drafting are two decisions with
  different economics; they got two settings, and the old one still reads as both.
- **A union of capabilities is a real answer.** What the page may offer is not what one provider can
  do — it is what each slot's provider can do for its own job.
- **A list of choices should not contain a choice that cannot work.** Deepgram is absent from the
  aligning slot, because it transcribes and says so.

### Decisions of 2026-09-27 (giving the card back, §9, slice 41)

- **An idle desktop app holds nothing it is not using.** The card is shared with whatever else the
  machine is doing, including the desktop itself.
- **Never import a heavy dependency to tidy up after it.** The release is a `sys.modules` lookup.
- **A timer must know when the thing is in use.** Found by running it: the first version took the
  models out of a request that was still being served.

### Decisions of 2026-09-30 (the box and the last line, §9, slice 96)

- **A control computed once is a control that is wrong later.** The height was right for the text the
  editor opened with and for no other.
- **Growing is not shrinking.** Both directions are "following the text"; only one of them moves what a
  person is reading.
- **Clamping hides the end.** `Math.min(i + 1, last)` looks safe and quietly turns "move on" into "do it
  again to the same line".
- **Refuse out loud, and only where it matters.** The last line said so; every other line still took a
  second stamp, because that is how a late one is fixed. *(Superseded the same day: with the caret
  staying put, a second stamp is the ordinary act everywhere and nothing refuses it.)*
- **A user's word about their own editor settles it.** Two packages had reasoned their way to the
  caret advancing — it is what stamping downwards needs — and the person doing the stamping wants the
  line they just set, because that is the one they are still listening to.

### Decisions of 2026-10-01 (a collection that cannot be replaced, §9, slices 99–101)

- **Prepare the way back before the way in.** The snapshot, the careful write and the kept originals
  were built and tested before the pass that needs them existed.
- **Prove the recording survived, not that the file did.** A tag write changes the file by design; what
  must never change is the audio, and that is what the digests are for.
- **Byte-identical is a promise only a copy can make.** A tag round-trip is not byte-identical, so the
  documentation says which layer promises what instead of promising the strongest thing everywhere.
- **Put the original aside before the first change**, under the name it had — not after the rename,
  where the restore cannot find it.
- **A quick decision tonight is not a policy for ever.** The take-in's switches do not follow the album
  around; the settings do.
- **An exception may only subtract.** Anything that could add would give the library a second state.
- **A pass over eleven thousand tracks will be interrupted.** Write down what is finished, album by
  album, and resume from it.

### Decisions of 2026-09-30 (a watched folder is a source, §9, slice 98)

- **Sort by what a thing *is*, not by where its status came from.** The watcher's rows lived under
  Library because that is where its status was reported; what they *are* is places music arrives.
- **One row per thing, and "one" is not the only number.** A provider is at most one; a folder is a
  row each, and the add-list offers another for ever.
- **Validate the whole set where the one is edited.** A folder's dialog judges the list it would make,
  not the folder alone — a clashing name or a nesting is somebody else's row's business.
- **An action is not a source.** Taking a folder in once stays under Library, beside the update and the
  repair.

### Decisions of 2026-09-30 (sources as a list, §9, slice 95)

- **Show what somebody has, not what they could have.** A settings page is a list of decisions already
  taken; the fields behind each one are a second question.
- **A dialog is a boundary.** One provider's session, one provider's switches — a page that can reach
  another provider's key from here is a page that can send it by accident.
- **A default is not a setting.** `post_cap = 200` was chosen by nobody, so it cannot make a source look
  configured.
- **Say what a removal costs before it happens**, and what still works afterwards: a source without a
  session is not a source that stopped working.
- **The page still does not know which sources exist.** Rows, dialogs and the add-list are all built from
  what the server answers, which it builds from the registry.

### Decisions of 2026-09-30 (the line stays in sight, §9, slice 94)

- **"It loses the focus" may not be about focus.** Measure `document.activeElement` before believing a
  focus report: here it never moved, and the fault was a scroll position.
- **Whoever moves a caret owns what can be seen.** A `setSelectionRange` is not a scroll, so a handler
  that moves the caret has to put the line in view itself.
- **One place for "put the caret here".** Four handlers each did their own focus-and-select; the one
  that also had to scroll would otherwise have been the only one that did.
- **Keep the behaviour the flow needs.** The advance to the next line is what makes stamping from the
  keyboard possible; the answer was to make it visible, not to undo it.

### Decisions of 2026-09-30 (a released snapshot, §9, slice 93)

- **A service must not run a working tree.** Not because it is untidy, but because a service that
  restarts on demand will eventually start mid-edit, and nothing about that failure looks like a cause.
- **A program may not replace the venv it is running from.** That is why the release tool is a script in
  the repository and not a subcommand of the thing being replaced.
- **Build the tag, not the tree.** `git archive <tag>` into a temporary directory makes "what was
  released" a fact rather than a hope about the checkout's state.
- **Refuse the cheapest mistake first.** The state of the tree is what a person gets wrong; a message
  about a venv path they never chose reads like a bug in the script.
- **A version is only half an answer.** "1.22.0" from an editable install is a claim about a directory
  somebody may be editing. Say which of the two it is, in the page and on the command line.
- **Ask `git describe` when somebody asks.** A lazy argparse action, not a string built on every
  invocation.
- **Measure the disk before claiming it is free.** uv hardlinks within one filesystem and copies across
  two: the same venv is tens of megabytes or seven gigabytes depending on where it is put.
- **A dependency of the machine is not a dependency of the package.** Two CUDA 12 wheels and a keyring
  client are true of this laptop, not of noaap; the release install is the one place that can say so.
- **Fail the release rather than install a slower one.** A missing helper that only shows up as "why is
  this suddenly on the processor" is worse than an install that stops and says which package it wanted.

### Decisions of 2026-09-30 (only what is needed, §9, slice 92)

- **A field that cannot matter is worse than a missing one.** It asks the user to decide something the
  program will never read, and it teaches them that the panel is noise.
- **Hide it, do not clear it.** What is not shown is not sent; what is not sent is left alone. Anything
  else loses an endpoint to a moment's curiosity about another provider.
- **The page must not know which sources exist.** It renders what the server answers, and the server
  reads the registry and the provider's own field names — which is also what keeps a provider's name out
  of the core.
- **A cookies file is a path.** The program reads it where it runs; nothing about its contents belongs in
  an answer to a browser.
- **A check and an apply belong to the same question.** The lines of a check are about one folder in one
  mode; change either and they are a story about something else.
- **Say what a pass writes on the other side.** "Never written to" is a promise only `merge` can make,
  so only `merge` makes it.
- **A refusal lives in one place.** The page, the door and the pass all ask `refuse_folder`, so no button
  offers what the server will decline.
- **Editing what a separate service reads is fine; pretending to be it is not.** The page writes the
  tables and keeps saying who does the watching.

### Decisions of 2026-09-30 (the bin's own place, §9, slice 91)

- **A settings panel is for settings.** Anything that holds the user's own things is a place of its own.
- **Offer it when there is something in it.** An empty bin is not news, and a button that is always
  there teaches people to ignore it.
- **A number in a header is polled**, so it must cost a directory listing and not a walk.
- **A word like "repair" is a promise about somebody's files**: say what it does where there is room to
  say it, and make it a check before it is an apply.
- **A header is for what you reach for without reading.** Anything that needs a paragraph belongs where
  there is room for the paragraph.
- **A modifier key is not a feature.** Shift+click to read every album in full was invisible; it is a
  button with a sentence now.
- **A dry run's list is only good until the library changes.** After that it is a story about a library
  that no longer exists, and offering to apply it is offering the wrong thing.

### Decisions of 2026-09-30 (what the first play was waiting for, §9, slice 90)

- **A lookup by id is an index, not a search.** Anything a page does per card will be done hundreds of
  times at once.
- **Anything served from a file may be cached**, and a server that sends `Last-Modified` must answer
  `If-Modified-Since`. Sending it and ignoring it is worse than not sending it.
- **Measure where the time goes, not that it is slow.** Time-to-first-byte against total time tells a
  busy server from a queued client, which is why both are in the log line.
- **A client that hangs up is normal traffic.** Reserve tracebacks for our own faults.

### Decisions of 2026-09-30 (a save reads the track rows, §9, slice 89)

- **A table's rows are not all the same kind of thing.** Whoever reads them says which kind they want.
- **An exception in a handler is a silent failure.** Every door a person presses reports what happened
  to it, in the page.

### Decisions of 2026-09-30 (a jump is answered where it was asked, §9, slice 88)

- **A fallback that changes which file is served must be visible to whoever is counting.** `o=1`
  answering with the cut file is fine; the page believing otherwise is not.
- **Nothing moves the playhead on its own.** Every seek has a caller who asked for it.
- **A user's jump is not a mistake to be corrected.** The guard skips a head somebody plays into, not
  a point somebody chose.
- **Say "not reproduced" and show the attempts.** 21 clicks in 8 states, with what would tell the
  difference next time.

### Decisions of 2026-09-30 (the window belongs to the loaded file, §9, slice 87)

- **A rule about a file must be keyed to the file in hand**, not to what a record says about it. The
  player knows which URL it asked for; that is the only trustworthy answer.
- **A URL must change when the bytes do.** Otherwise the media cache is free to serve what it has.
- **Do not swap a playing source to tell the page something.** Update what the page knows; let the
  sound finish.
- **A refresh that depends on seeing a job run will miss the short jobs.** Refresh from the job's own
  ending.
- **Measuring the number is not measuring the thing.** I read "playback starts at 2.48 s" as correct in
  P69 without asking which file was playing; it was the original, and those 2.48 s included audio the
  user had cut away.

### Decisions of 2026-09-30 (a cut file starts at zero, §9, slice 86)

- **Measure on the files the user has, not on the files the tests make.** Fifteen clean playback
  measurements meant nothing because none of them was a cut file.
- **A window already applied to a file is not a window.** Whoever holds a rule about a recording must
  know whether the recording has already been changed by it.
- **`-ss` belongs after `-i` for a cut**, and a cut file's clock starts at zero. A player's clock that
  runs past the duration it was given is a bug wherever it shows up.
- **Reject a candidate fix out loud.** `make_zero` on an input seek looks right and keeps a second of
  the audio the user cut away.
- **An album whose plan is tidy can still have work in its files.** The skip has to know that.

### Decisions of 2026-09-30 (a dry run that says everything, §9, slice 85)

- **A dry run is a promise.** Whatever the real run does must be in it, and the way to keep that true
  is to build the report from the pass's own predicates and to compare the two sets in a test.
- **Say the thing the person is afraid of.** Album and track counts are not an answer to "will you
  write into my files"; the number of files that would be rewritten is.
- **A diff of two long values shows where they differ**, not their first sixty characters.
- **One text, one shape.** A value that is written to disk and compared on the next pass must be
  normalised where it enters, or the comparison is false for ever.
- **Measure the owner of a warning before fixing it.** The semaphore was tqdm's, the hub greeting was
  demucs's; neither was where it looked.

### Decisions of 2026-09-30 (what a job holds and what it asks for, §9, slice 84)

- **A background thread with no lifecycle is global state.** One `App` per process is right; 59 of
  them in a test session reached into whichever test had stubbed `sys.modules`, and the symptom was a
  release counter reading three (`docs/qa-catalog.md`, BX). Fix the thread, not the assertion.

- **A held model is room for the job that reuses it and an obstacle to the job that does not.** The
  gate has to know which, and the job has to let go before it asks.
- **The first line of a job says what it just did to the machine**, not only what it is about to do.
- **Cached means offline.** A local provider that reaches for the network with the file on the disk is
  not local; a first download is fine and says so.
- **Measure which component does it.** The hub greeting was blamed on the big model for a whole
  package; it was the separator.

### Decisions of 2026-09-30 (placing words by listening, §9, slice 83)

- **A method that can say "these words are not in this recording" is worth having even at half the
  recall**, because the alternative is a confident wrong answer.
- **The matching belongs in the core, not in a provider.** It is normalised strings and `difflib`;
  every provider that transcribes with word times gets the method for free.
- **Where a knob does not change the outcome, say so and stop turning it.** The sweep over both
  thresholds moves accuracy not at all; the limit is the transcript.
- **Never tell a model something you are not sure of.** The words name their language only when two
  stopword lists agree clearly; otherwise the model detects for itself.
- **What to listen to is a measurement, not a principle.** The local model wants the track; a vendor
  wants the isolated voice. Both numbers are in the catalog.
- **The mark says which question was asked**, not only who answered it.

### Decisions of 2026-09-30 (the app gives the card back, §9, slice 82)

- **Measure before choosing the number.** The idle window is 60 s because a reload costs 2 s and a
  hold costs 3.4 GB, not because a minute sounds reasonable.
- **A tidy-up that runs only when it happens to be convenient is not a tidy-up.** The old release ran
  once, after a job, and only if nothing else was queued; nothing retried it. A watch retries.
- **The hold belongs to whoever runs the jobs**, not to a module-level cache: one per server, one per
  CLI pass, so nothing leaks between two programs or two tests.
- **Check and release in one step**, inside the lock a job must pass to start.
- **A full card is a slower job, not a failed one** — and the job says which it is in its first line.
- **One sentence for a person, never a traceback in a page.**

### Decisions of 2026-09-27 (giving the words back, §9, slice 42)

- **A publish is public and permanent, so the page says everything before it happens** — and says
  why, whenever it will not offer to.
- **Never send LRCLIB its own words back.** Saving their entry unchanged makes it yours by
  provenance; it does not make it new to them.
- **One press, one request, one possible copy.** The publish POST is never retried.
- **Record a fingerprint, not the text.** It answers "these again?" and nothing else.

### Decisions of 2026-09-27 (offering an album, §9, slice 43)

- **An edit belongs to the person making it.** Seeding a form, never submitting one: no credentials
  in ytalbum, and nothing reaches MusicBrainz that a person has not read.
- **Offer only what they would want.** Compilations and hand-made playlists are not releases, and
  the refusal says so where the button would have been.
- **One place for one fact.** The length chip became the way to a recording's page rather than
  gaining a neighbour that printed its numbers again.

### Decisions of 2026-09-27 (which method lost the song, §9, slice 44)

- **Evidence where there is any, policy where there is none.** The rule speaks only when exactly one
  method looks lost; otherwise it says so and falls back to what it did before.
- **A signal about the shape of a lyric, not a model's opinion of itself.** Coverage of the singing
  is checkable by a person with the track in front of them; a confidence number is not.
- **Record what was measured, judge only what works.** Silence and confidence stay in the
  provenance as numbers; neither decides anything.
- **Collect the cases the measurement could not have.** Saved alignments carry both methods' figures,
  so the thresholds can be widened from the library instead of from another sixteen copies.

### Decisions of 2026-09-27 (a draft that reads like a song, §9, slice 45)

- **Break where the singing pauses.** Punctuation is the vendor's; pauses are the song's.
- **Say what was not heard.** A hole in a draft is invisible unless it is written down.
- **Count what the machine covered, not what it emitted.** "N of N lines came with a time" is a
  sentence that cannot be false.
- **Give the transcriber the voice.** It doubles what a vendor hears on real material, and it sends
  less of the recording away.

### Decisions of 2026-09-27 (an entry that is nearly this recording, §9, slice 46)

- **Measure the thing you care about.** "Is this the same recording" is answered by aligning the
  words to the audio, not by comparing two durations.
- **One instrument, two questions.** Unplaced lines say whether the words are the song's; the span
  says whether the clock is this cut's. Keeping them apart is what makes three outcomes possible.
- **A guard before an expensive test.** ±25% costs nothing and saves an alignment that could only
  have said "no".
- **Where the instrument does not exist, say so.** Without an aligner nothing new is taken and the
  panel tells the user the words exist anyway.
