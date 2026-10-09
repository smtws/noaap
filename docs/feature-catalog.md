# What noaap does, feature by feature

Written from the code, not from memory: the commands and their flags come from argparse's own
help, the HTTP surface from `web.py`, the settings from `Config`, the page's controls from
`app.js`, the providers from the modules that implement them. Regenerate with
`scratchpad/p115/catalog.py` (R-543).

**221 user-visible features**, enumerated: **21 commands** with **95 flags** and 13 subcommands, **13 HTTP endpoints**, **24 write actions**, **41 settings**, **5 timing providers**, **4 audio sources**, and the page's five areas (its individual buttons are grouped under those, not counted one by one).

## Getting music in

- **`noaap fetch`** — plan and download a playlist, video or channel URL  — tests: **G2**
  - flags: `--all` (G2), `--dry-run` (G2), `--dump-collection` (G2), `--library` (G2), `--no-lyrics` (G2), `--no-mb` (G2), `--pick` (G2)
- **`noaap search`** — find an artist's albums on YouTube and pick which to fetch  — tests: **G3**
  - flags: `--all` (G3), `--dry-run` (G3), `--library` (G3), `--no-lyrics` (G3), `--no-mb` (G3), `--pick` (G3)
- **`noaap plan`** — write the plan into the album folder for editing, download nothing  — tests: **G1**
  - flags: `--library` (G1), `--no-mb` (G1), `--verify` (G1)
- **`noaap download`** — download from an (edited) plan in an album folder  — tests: **G1**
  - flags: `--no-lyrics` (G1)
- **`noaap watch`** — watch the configured folders and hand what arrives to the app  — tests: **E1**
  - flags: `--interval` (E1), `--once` (E1), `--port` (E1), `--settle` (E1)

## Taking an existing collection in

- **`noaap take-in`** — take a whole collection in and bring every album to one state  — tests: **A1**
  - flags: `--apply` (A1), `--batch-size` (A1), `--keep-originals` (A1), `--names` (A1), `--no-cover` (A1), `--no-embed-cover` (A1), `--no-embed-lyrics` (A1), `--no-lyrics` (A1), `--no-mb` (A1), `--no-resume` (A1), `--no-tags` (A1), `--only` (A1), `--restore` (A1), `--snapshot` (A1), `--staging` (A1)
- **`noaap adopt`** — take a collection in where it stands: one plan per album, nothing else  — tests: **A3**
  - flags: `--album` (A3), `--apply` (A3), `--library` (A3), `--only` (A3), `--rename` (A3), `--retag` (A3), `--undo` (A3)
- **`noaap merge`** — take the better copies out of another library (shows first)  — tests: **A4**
  - flags: `--album` (A4), `--apply` (A4), `--library` (A4), `--new` (A4), `--only` (A4), `--rejudge` (A4), `--undecided` (A4)
- **`noaap migrate`** — take over what ytalbum left on this machine (shows first)  — tests: **G4**
  - flags: `--apply` (G4), `--uninstall-old` (G4)

## Keeping the library right

- **`noaap update`** — re-check every album in the library against its source  — tests: **B1**
  - flags: `--deep` (B1), `--dry-run` (B1), `--library` (B1), `--no-lyrics` (B1), `--no-mb` (B1), `--only` (B1)
- **`noaap repair`** — tidy artist names and measure files in the library, offline  — tests: **B2**
  - flags: `--apply` (B2), `--dry-run` (B2), `--find-moved` (B2), `--library` (B2), `--only` (B2), `--strays` (B2), `--under` (B2)
- **`noaap lyrics`** — fetch lyrics for tracks that have none yet (.lrc beside the file + tag)  — tests: **B3**
  - flags: `--artist` (B3), `--dry-run` (B3), `--library` (B3), `--near` (B3), `--refetch` (B3)
- **`noaap prune`** — delete the tracks that are no longer in the source playlist  — tests: **B5**
  - flags: `--yes` (B5)
- **`noaap delete`** — delete an album (or one track) — files are removed  — tests: **B4**
  - flags: `--track` (B4), `--yes` (B4)
- **`noaap recycle`** — what noaap moved aside instead of deleting  — tests: **B4**
  - subcommands: `list` (B4), `restore` (B4), `empty` (B4)
  - flags: `--library` (B4), `--older-than` (B4)

## Running it

- **`noaap serve`** — web UI for the library (also installable as an app)  — tests: **C1**
  - flags: `--host` (C1), `--idle-exit` (C1), `--library` (C1), `--port` (C1)
- **`noaap service`** — run the web UI on demand via systemd (user level)  — tests: **E4**
  - subcommands: `install` (E4), `uninstall` (E4), `status` (E4), `restart` (E4)
  - flags: `--force` (E4), `--from-checkout` (E4), `--idle-exit` (E4), `--port` (E4)
- **`noaap app`** — desktop launcher with its own window, not another browser window  — tests: **G5**
  - subcommands: `install` (G5), `uninstall` (G5), `status` (G5)
  - flags: `--browser` (G5), `--port` (G5), `--remove-profile` (G5)
- **`noaap watch-service`** — the watcher as its own systemd user service  — tests: **E1**
  - subcommands: `install` (E1), `uninstall` (E1), `status` (E1)
  - flags: `--from-checkout` (E1), `--port` (E1)
- **`noaap timing-serve`** — run the local aligner as a small HTTP service for another machine  — tests: **E2**
  - flags: `--device` (E2), `--host` (E2), `--port` (E2)
- **`noaap config`** — show or set configuration  — tests: **E3**
  - flags: `--cookies-file` (E3), `--cookies-from-browser` (E3), `--library` (E3), `--lyrics` (E3)

## The web UI

- **read endpoints**: `/api/album` (G6), `/api/audio` (C5), `/api/cancel` (G6), `/api/cover` (C2), `/api/details` (G6), `/api/job` (G6), `/api/lyrics` (G6), `/api/mbseed` (G6), `/api/recycle` (G6), `/api/settings` (G6), `/api/state` (C1), `/api/thumb` (G6), `/api/tracks` (C1)
- **write actions (`POST /api/<action>`)**: `align` (C4 G11), `arrived` (C4 E1), `check_lyrics` (C4 G7), `cover` (C2 C4), `delete_album` (C4 G8), `delete_track` (C4 G8), `draft` (C4 G11), `edit` (C4 G7), `empty_recycle` (C4 G8), `except` (C4 G7), `fetch` (C4 G8), `identify` (C3 C4), `lyrics` (C4), `lyrics_track` (C4 G7), `open` (C4 G8), `prune` (C4 G8), `publish_lyrics` (C4 G7), `repair` (C4 G8), `restore` (C4 G8), `save_lyrics` (C4 G7), `take_in` (C4 G8), `take_plain_lyrics` (C4 G7), `trim_channel` (C4 G7), `update` (C4 G8)
- **controls on the page**: 29 labelled buttons, 21 elements with their own key/input handling (`#artist-all`, `#artist-new`, `#artist-update`, `#bin`, `#gear`, `#grid`, `#length-filter`, `#libfilter`, `#needs-you-filter`, `#open`, `#p-from-start`, `#p-next`, `#p-play`, `#p-pos`, `#p-prev`, `#p-set-end`, `#p-set-start`, `#p-trim-clear`, `#p-trim-save`, `#play-matches`, `#theme`)

## Settings

- `concurrency` — G9
- `cookies_file` — — needs the user's own cookie jar; `config` writing it is covered by E3
- `cookies_from_browser` — — needs the user's own browser profile
- `cover_beside` — C2 G9
- `cover_embedded` — C2 G9
- `drop_comments` — G9
- `drop_wm_frames` — G9
- `js_runtime` — G11
- `js_runtime_path` — G11
- `library_root` — G9
- `lyrics` — G9
- `lyrics_embedded` — G9
- `musicbrainz` — G9
- `patreon_audio_from_video` — F2
- `patreon_captions` — F2
- `patreon_cookies_file` — — needs the user's own Patreon session
- `patreon_cookies_from_browser` — — needs the user's own browser profile
- `patreon_post_cap` — F2
- `pot_idle` — — same
- `pot_mode` — — the PO-token helper needs a node runtime and a live YouTube challenge
- `pot_port` — — same
- `pot_provider_home` — — same
- `remove_empty_folders` — G9
- `rename_adopted` — G9
- `retag_adopted` — G9
- `soundcloud_cookies_file` — — needs an account
- `soundcloud_cookies_from_browser` — — needs an account
- `tidy_adopted_tags` — G9
- `timing_align_provider` — G10
- `timing_card_idle_seconds` — G10
- `timing_deepgram_key` — — a secret; E3 proves no `*_key` value is ever printed
- `timing_device` — G10
- `timing_draft_provider` — G10
- `timing_elevenlabs_key` — — a secret; same as above
- `timing_endpoint` — E2
- `timing_idle_minutes` — G10
- `timing_provider` — G10
- `timing_verify` — G10
- `timing_verify_lost` — G10
- `timing_verify_threshold` — G10
- `watches` — — the value is covered by E1; editing it in the UI is the reviewer's D-set

`concurrency`, `cookies_file`, `cookies_from_browser`, `cover_beside`, `cover_embedded`, `drop_comments`, `drop_wm_frames`, `js_runtime`, `js_runtime_path`, `library_root`, `lyrics`, `lyrics_embedded`, `musicbrainz`, `patreon_audio_from_video`, `patreon_captions`, `patreon_cookies_file`, `patreon_cookies_from_browser`, `patreon_post_cap`, `pot_idle`, `pot_mode`, `pot_port`, `pot_provider_home`, `remove_empty_folders`, `rename_adopted`, `retag_adopted`, `soundcloud_cookies_file`, `soundcloud_cookies_from_browser`, `tidy_adopted_tags`, `timing_align_provider`, `timing_card_idle_seconds`, `timing_deepgram_key`, `timing_device`, `timing_draft_provider`, `timing_elevenlabs_key`, `timing_endpoint`, `timing_idle_minutes`, `timing_provider`, `timing_verify`, `timing_verify_lost`, `timing_verify_threshold`, `watches`

## Providers

- **timing / transcription**: `none` (G11), `local` (F1 G11), `http` (E2), `elevenlabs` (— needs a paid account and sends audio away; the boundary is covered by F2), `deepgram` (— R-543 forbids Deepgram calls tonight; its request shape is covered by the stored replies in P112)
- **audio sources**: `folder` (A1), `patreon` (F2), `soundcloud` (— needs an account cookie the user has not set), `youtube` (G2)

---


**221 features**, of which **205** are covered by a test in `night-tests.md` and **16** are named there with the reason they are not. Checked by `scratchpad/p115/cover.py`, which fails on a feature that is neither.
