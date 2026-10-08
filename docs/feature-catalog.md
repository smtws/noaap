# What noaap does, feature by feature

Written from the code, not from memory: the commands and their flags come from argparse's own
help, the HTTP surface from `web.py`, the settings from `Config`, the page's controls from
`app.js`, the providers from the modules that implement them. Regenerate with
`scratchpad/p115/catalog.py` (R-543).

**21 commands** with **95 flags**, **13 HTTP endpoints** and **24 write actions**, **41 settings**, **5 timing providers**, **4 audio sources**.

## Getting music in

- **`noaap fetch`** — plan and download a playlist, video or channel URL
  - flags: `--all`, `--dry-run`, `--dump-collection`, `--library`, `--no-lyrics`, `--no-mb`, `--pick`
- **`noaap search`** — find an artist's albums on YouTube and pick which to fetch
  - flags: `--all`, `--dry-run`, `--library`, `--no-lyrics`, `--no-mb`, `--pick`
- **`noaap plan`** — write the plan into the album folder for editing, download nothing
  - flags: `--library`, `--no-mb`, `--verify`
- **`noaap download`** — download from an (edited) plan in an album folder
  - flags: `--no-lyrics`
- **`noaap watch`** — watch the configured folders and hand what arrives to the app
  - flags: `--interval`, `--once`, `--port`, `--settle`

## Taking an existing collection in

- **`noaap take-in`** — take a whole collection in and bring every album to one state
  - flags: `--apply`, `--batch-size`, `--keep-originals`, `--names`, `--no-cover`, `--no-embed-cover`, `--no-embed-lyrics`, `--no-lyrics`, `--no-mb`, `--no-resume`, `--no-tags`, `--only`, `--restore`, `--snapshot`, `--staging`
- **`noaap adopt`** — take a collection in where it stands: one plan per album, nothing else
  - flags: `--album`, `--apply`, `--library`, `--only`, `--rename`, `--retag`, `--undo`
- **`noaap merge`** — take the better copies out of another library (shows first)
  - flags: `--album`, `--apply`, `--library`, `--new`, `--only`, `--rejudge`, `--undecided`
- **`noaap migrate`** — take over what ytalbum left on this machine (shows first)
  - flags: `--apply`, `--uninstall-old`

## Keeping the library right

- **`noaap update`** — re-check every album in the library against its source
  - flags: `--deep`, `--dry-run`, `--library`, `--no-lyrics`, `--no-mb`, `--only`
- **`noaap repair`** — tidy artist names and measure files in the library, offline
  - flags: `--apply`, `--dry-run`, `--find-moved`, `--library`, `--only`, `--strays`, `--under`
- **`noaap lyrics`** — fetch lyrics for tracks that have none yet (.lrc beside the file + tag)
  - flags: `--artist`, `--dry-run`, `--library`, `--near`, `--refetch`
- **`noaap prune`** — delete the tracks that are no longer in the source playlist
  - flags: `--yes`
- **`noaap delete`** — delete an album (or one track) — files are removed
  - flags: `--track`, `--yes`
- **`noaap recycle`** — what noaap moved aside instead of deleting
  - subcommands: `list`, `restore`, `empty`
  - flags: `--library`, `--older-than`

## Running it

- **`noaap serve`** — web UI for the library (also installable as an app)
  - flags: `--host`, `--idle-exit`, `--library`, `--port`
- **`noaap service`** — run the web UI on demand via systemd (user level)
  - subcommands: `install`, `uninstall`, `status`, `restart`
  - flags: `--force`, `--from-checkout`, `--idle-exit`, `--port`
- **`noaap app`** — desktop launcher with its own window, not another browser window
  - subcommands: `install`, `uninstall`, `status`
  - flags: `--browser`, `--port`, `--remove-profile`
- **`noaap watch-service`** — the watcher as its own systemd user service
  - subcommands: `install`, `uninstall`, `status`
  - flags: `--from-checkout`, `--port`
- **`noaap timing-serve`** — run the local aligner as a small HTTP service for another machine
  - flags: `--device`, `--host`, `--port`
- **`noaap config`** — show or set configuration
  - flags: `--cookies-file`, `--cookies-from-browser`, `--library`, `--lyrics`

## The web UI

- **read endpoints**: `/api/album`, `/api/audio`, `/api/cancel`, `/api/cover`, `/api/details`, `/api/job`, `/api/lyrics`, `/api/mbseed`, `/api/recycle`, `/api/settings`, `/api/state`, `/api/thumb`, `/api/tracks`
- **write actions** (`POST /api/<action>`, all requiring the `X-Noaap` header and a JSON body): `align`, `arrived`, `check_lyrics`, `cover`, `delete_album`, `delete_track`, `draft`, `edit`, `empty_recycle`, `except`, `fetch`, `identify`, `lyrics`, `lyrics_track`, `open`, `prune`, `publish_lyrics`, `repair`, `restore`, `save_lyrics`, `take_in`, `take_plain_lyrics`, `trim_channel`, `update`
- **controls on the page**: 29 labelled buttons, 21 elements with their own key/input handling (`#artist-all`, `#artist-new`, `#artist-update`, `#bin`, `#gear`, `#grid`, `#length-filter`, `#libfilter`, `#needs-you-filter`, `#open`, `#p-from-start`, `#p-next`, `#p-play`, `#p-pos`, `#p-prev`, `#p-set-end`, `#p-set-start`, `#p-trim-clear`, `#p-trim-save`, `#play-matches`, `#theme`)

## Settings

`concurrency`, `cookies_file`, `cookies_from_browser`, `cover_beside`, `cover_embedded`, `drop_comments`, `drop_wm_frames`, `js_runtime`, `js_runtime_path`, `library_root`, `lyrics`, `lyrics_embedded`, `musicbrainz`, `patreon_audio_from_video`, `patreon_captions`, `patreon_cookies_file`, `patreon_cookies_from_browser`, `patreon_post_cap`, `pot_idle`, `pot_mode`, `pot_port`, `pot_provider_home`, `remove_empty_folders`, `rename_adopted`, `retag_adopted`, `soundcloud_cookies_file`, `soundcloud_cookies_from_browser`, `tidy_adopted_tags`, `timing_align_provider`, `timing_card_idle_seconds`, `timing_deepgram_key`, `timing_device`, `timing_draft_provider`, `timing_elevenlabs_key`, `timing_endpoint`, `timing_idle_minutes`, `timing_provider`, `timing_verify`, `timing_verify_lost`, `timing_verify_threshold`, `watches`

## Providers

- **timing / transcription**: `none`, `local`, `http`, `elevenlabs`, `deepgram`
- **audio sources**: `folder`, `patreon`, `soundcloud`, `youtube`

---

**245 user-visible features counted** by the list above.
