# YT-Downloads v3 — Design

Status: draft, 2026-09-22. Replaces `ARCHITECTURE_PLAN.md` (v2) and the v1 tree in
`~/YT-Downloads-master`. Nothing from v1/v2 is carried over as code unless listed under
"Salvage" at the end.

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
   `.pot-provider/server` (v2.0.0, Node, gitignored). ytalbum detects it and passes
   `youtubepot-bgutilscript:server_home`. Result: Opus 251 offered, Feuerschwanz track
   downloaded at 121 kbps. Script mode costs a Node process per request (a full 4-album
   update took 2.5 min), so the default is now **server mode** (`pot.py`): before reading
   or downloading, ytalbum pings `127.0.0.1:4416/ping` and, if nothing answers, starts a
   detached watchdog (`python -m ytalbum.pot`) running `node build/main.js` on localhost
   only; every YouTube request and download progress touches
   `~/.cache/ytalbum/pot-server.heartbeat`, and after `pot_idle` (300 s) without a beat the
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
   (`.ytalbum.json`) — this is also the manifest for incremental re-runs.
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
- Schema changes bump `schema` and come with a migration of `.ytalbum.json`; there is
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
  ytalbum only puts it beside a file the user already has, and `--no-lyrics` / `config
  --lyrics off` switches it off.
- ffmpeg only for remux (`-c:a copy`) and chapter splitting.
- Config: one TOML file (`~/.config/ytalbum/config.toml`), overridable per run by CLI
  flags. Holds `library_root` (**configurable, no default path baked into code**; the
  first run asks, or takes `--library`), filename template, compilation naming rules,
  MB on/off, concurrency, JS runtime.
- UI: **CLI first** (`ytalbum fetch <url> [--dry-run] [--edit]`). Web/PWA later on top
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
3. ✅ Incremental re-run from `.ytalbum.json` (Vol. 20 grows → only new tracks). *Done
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
   is not kept in `.ytalbum.json` (3946 × ~3 KB would land in every `update` and in the track
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
   contract of §9.21 is about words a user writes by hand, and the only door to it was the file
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
   track (one whose `ffmpeg` was missing when it was set, §9.20) is applied then — the same thing
   any other pass would have done, reached from a new direction. The write itself is a job in
   the write lane like every other library change, and jobs now carry the album they hold
   (`Job.target`), so a save is **refused** while a pass is working on that album instead of racing
   it — a pass would retag from the very file the save is about to write. A `fetch` is named by its
   URL and only learns the album id while it runs, so it is not one of the jobs that check can see;
   that is a known gap, not a silent one. A track that is not `done` has no file to put words
   beside and is refused too.
   Nothing about the contract needed a special case for the editor: a sidecar it wrote, then edited
   again on disk, is still the user's, and deleted on disk it follows §9.21 like any other.

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
   Rejected ids are dropped from the length consensus too (§9.24), not only from the words: an entry
   that is not this song is no evidence about how long this song is either.
   Neither action is offered for lyrics marked as the user's — those are not LRCLIB's to replace, and
   the editor's Delete is the way to let it answer again (§9.26). Both are write jobs with the album
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
   Lyrics are deliberately not in this: the editor's Delete is their way back (§9.26), and two
   affordances for one thing would only be two things to explain.
   Measured read-only over the library this is for: 59 of 246 albums carry at least one overridden
   field — 38 album artists, 20 years, 15 user orders, 3 album names — and 83 tracks (73 artists,
   17 titles). Every one of them has an `auto` value behind it, so every one is resettable.

30. ✅ Opening an album asks the disk (2026-09-26, backlog item 6). Ownership of an edited `.lrc`
   and the status of a deleted one were only noticed when some pass walked the album, so between
   passes a row could show ♪ for words that were no longer there — safe, because `reconcile` runs
   before anything overwrites a file (§9.21), but a user would call it a bug. `/api/album` now runs
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
   trim points mean (§9.17) — the test for that case is the one that would catch a future refactor.
   **Which file you are hearing.** A cut track is played from its kept original, or the head would be
   skipped twice (§9.17); the player now says so, and *▶ from start* plays from the start mark, which
   is the question a start mark actually raises ("does the song begin here?").
   Marks are rounded to a tenth. `audio.currentTime` carries a dozen decimals of mouse precision that
   mean nothing musically and end up in the plan and on ffmpeg's command line.
   Deliberately not here: automatic cut detection and a waveform. §9.8 measured detection and dropped
   it, and a chip that leads to a cut does not need to guess the cut.
   The JS half has no unit tests, because this repo has no JavaScript test harness and P14 is not the
   place to introduce one; `plan.trimmed_gap` carries the arithmetic under test, and the browser
   checks in the catalog (section P) are the evidence for the rest.

32. ✅ Rows are dragged, and a typed number counts in the disc you put the track on (2026-09-26,
   backlog item 4, the last of them). Typed positions have landed correctly since §9.22, but typing
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
   the track was, which is what keeps §9.22's collapse from interleaving the two discs. One line, one
   function, and the drag lands.
   One case only the browser could show: a row dragged into another disc often keeps its *per-disc*
   number by coincidence — 2-02 dropped at 1-02 is still "2" — so a changed number cannot always
   say that the user moved it, and without that knowledge it was read as a row that stayed put and
   filed after its new disc's rows. The payload now carries `moved` for rows the user has actually
   put somewhere since the last save, which is the UI stating an intent instead of the server
   inferring one. A collapse sends no `moved`, so §9.22's case is untouched.
   That also changed a case reviewed in P3: typing "1" while collapsing two discs into one used to put
   the track first of its *former* disc-2 block (seventh), and now puts it first of the album. Under
   one disc, "1" means first; the old reading was defensible only while numbers were read under the
   old discs for every purpose. The test carries the reasoning.

33. ✅ The page's own logic has tests (2026-09-26, backlog item 10, the user's call after two UI
   defects shipped green). `app.js` had grown to ~1,500 lines carrying real rules — the length
   target, the arrangement and its live renumbering, what a panel offers, the filter's folding — and
   none of it ran under a test. What it *did* have was the Python twins and the Playwright cases,
   which is why the two defects that shipped (§9.29's invisible badge, §9.31's scattered buttons)
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
   side fails on the other. §9.24's rounding, §9.22's per-disc numbering, §9.32's drop semantics,
   §9.21's panel rules and §9.29's badge decision are pinned the same way.
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
   (`provenance.source = user`, `auto.source` = the playlist id), so §9.29's badge is the way back
   with no new mechanism behind it.
   **A source change is a re-download, and it says so first.** Everything the old file carried is
   about a different recording: the state, the tags, the trim marks, the measured length, and the
   uploader and duration, which follow the audio (a channel-wide trim must not cut this file to
   another channel's ident). The kept original of the previous source is deleted — it could only ever
   shadow the new one — and originals are keyed by the effective id, so going back re-downloads
   cleanly. The UI confirms with the marks named in it ("The trim 1:30–5:20 belongs to the current
   file and will be cleared"), because a mark silently kept would cut the wrong seconds.
   **The words are never touched, and the timings say what they were written for.** §9.21 stands: the
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
   `tests/shared/video_ids.json` is the one table both are tested against (§9.33).

35. ✅ Stamping the words to the file's own clock (2026-09-27, P23, backlog 13). The user timed
   "Und 'n Tripper" by hand and every stamp landed up to three seconds late. Three causes, measured
   in that order: the track is trimmed from 1.6 s and **a trimmed track is played from its untouched
   original** (§9.15 — the marks count from the start of the video, so the player must hear the file
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
   reaches the disk until Save, so the ownership contract (§9.21, §9.26) is untouched and a stamp
   written by the tap is byte-for-byte what a hand-typed `[mm:ss.t]` would be — verified by reading
   the saved `.lrc` back.
   **The offset is the trim the file was *cut* to, not a mark being placed.** `trimOffset` reads the
   saved mark, because the `.lrc` belongs to the file on disk; the two differ exactly while someone
   is dragging a trim handle. The same helper now serves the seek and the sung-line highlight, which
   both used the pending mark before and were a fraction out in that one state.
   Deliberately unchanged: the player's own display and the trim bar keep the original's clock
   (outcome 6 of the task, and §9.15's reason — the trim marks depend on it).

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
   plays a line, nudges it, shifts it (§9.35) and presses Save, which is `save_lyrics` exactly as
   before. So the ownership contract (§9.21) needed no new rule: the words stay the user's, the
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
   decided the question §9.36 left open — *the audio comes from YouTube anyway, so it may leave the
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
   whose words they were, beside §9.36's `lyrics_timed_by` for whose clock, and the panel shows
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
   to (§9.35). `YTALBUM_TIMING_BASE_<VENDOR>` redirects a client at another host: a gateway, a proxy,
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
   clock exactly as a stamp is (§9.35). Clicking a line seeks to it. Nothing about the saved file
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
  decides, and where there is no record, lrclib is asked about the entry we stored (§9.21).
- Deleting your own lyric gives the mark up with it, so `--refetch` can answer again (§9.21).
- The lyrics panel **writes**, and the editor never looks anything up — a save can then never be
  answered by replacing the words just typed (§9.26).
- A rejected lrclib entry is remembered **per track** and never offered for it again, which is what
  a deleted file could not say (§9.27).
- A fetch renames only the album it is fetching; the library's spelling wins and `repair` is what
  upgrades the rest (§9.23).
- `repair` decides each artist's spelling once, before it renames anything (§9.23).
- A length reference lrclib contributes is the **consensus** of its candidates, not the nearest
  one, and the query drops only the instrumental markers (§9.24).
- A single's album name follows its own track's title (§9.25).
- **Repair is reachable from the web UI, behind a confirm rather than a preview** (P10, catalog L):
  a preview would need a pass that reports without writing, which is the fetch preview's job and
  not repair's. This decision lives nowhere else.
- The fetch preview merges with what is in the library, so it shows what a fetch would write rather
  than a fresh reading of YouTube; Shift+click skips it (§9.28).
- Resetting a field drops its provenance rather than guessing it, because `auto` records the derived
  value and never its source (§9.29).
- Opening an album reconciles its lyrics with the disk; the library grid deliberately does not
  (§9.30).
- A number the user **typed** counts in the disc they are putting the track on; one left alone counts
  where the track was (§9.32).
- **No JavaScript test harness inside a feature package** (§9.31): the arithmetic lives in Python
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

### Decisions of 2026-09-27 (an alternative source per track, §9.34)

- **The playlist video is the identity; only the audio may be pointed elsewhere** (§9.34). Replacing
  `video_id` would have been fewer lines and would have broken `in_source`, prune, the merge and the
  MusicBrainz match, all of which are about *which entry this is*, not about which file plays.
- **A source change clears the trim rather than keeping or converting it.** Marks are seconds of a
  particular recording; the new one has its own silence at the front. Converting them would be a
  guess, keeping them would cut the wrong audio, so they go — and the UI names them before it asks.
- **The uploader follows the audio, not the identity** (§9.34): "trim everything from this channel"
  is about who encoded the file in front of you. It is re-read from the video actually used, in both
  directions, and a merge no longer overwrites it from the playlist entry.
- **No automatic re-timing of a user's lyrics.** The notice states the problem and leaves the fix to
  them; doing it for them is P23, where it can be confirmed and undone.

### Decisions of 2026-09-27 (stamping to the file's clock, §9.35)

- **The tools act on the line the cursor is in, not on a widget per stamp.** The editor is a
  textarea and stays one (the task pins it as the source of truth), so per-stamp buttons would mean
  a second representation of the same text and two ways for them to disagree. The cursor already
  decides the line for the tap; play, nudge and shift use the same rule.
- **The stamp is a tenth.** `audio.currentTime` carries a dozen decimals of nothing, LRC players
  read hundredths, and a tenth is finer than anyone can tap — the same decision §9.31 took for the
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

### Decisions of 2026-09-27 (the timing boundary, §9.36)

- **Built as the spike recommended, with two providers rather than one** (P25): `local` for whoever
  has the extra, and `http` + `ytalbum timing-serve` because "run the inference on the other
  machine" is this household's actual deployment and is fifty lines around the same code.
- **The provider never writes.** It answers with lines and a `Timed` record of itself; the editor
  shows them and the user saves. That is what keeps §9.21 untouched and makes a wrong alignment cost
  a "Cancel" rather than a restore.
- **`unplaced` is part of the answer**, not an error. A provider that cannot say which lines it
  would not place cannot be trusted with the rest.
- **The words choose the aligner.** The language comes from counting stopwords in the lyrics, as the
  spike did, rather than from a configuration key or a language-detection dependency.
- **No transcription and no library-wide pass in this package.** Both are decisions of their own
  (backlog 15 and 16), and the measurements do not support making them quietly.

### Decisions of 2026-09-27 (paid providers, §9.37)

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

### Decisions of 2026-09-27 (a second opinion, §9.38)

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

### Decisions of 2026-09-27 (the editor's own clock, §9.39)

- **Two views must not be fed by two truths.** While the editor is open there is exactly one truth
  for playback, and it is the textarea. The file is the truth again the moment the editor closes.
- **One door for every rewrite.** Assigning `.value` fires no event; a redraw that listens for one
  therefore has to be told. `editorText()` exists so that no tool can quietly stop being seen.
- **A second copy of the words needs a name.** Found by looking at a screenshot, which is where the
  page's unlabelled things are always found.

### Decisions of 2026-09-27 (two slots, §9.40)

- **One setting per decision, not per subsystem.** Aligning and drafting are two decisions with
  different economics; they got two settings, and the old one still reads as both.
- **A union of capabilities is a real answer.** What the page may offer is not what one provider can
  do — it is what each slot's provider can do for its own job.
- **A list of choices should not contain a choice that cannot work.** Deepgram is absent from the
  aligning slot, because it transcribes and says so.
