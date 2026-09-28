# Real plans, one per shape

Copied read-only out of the reference library on 2026-09-28, so that a refactor can prove it
reads and writes them exactly as v0.7.0 does. No filesystem path appears in any of them (the
`folder` and `filename` fields are relative names, which is what they are on disk); no covers
and no audio.

Two are **built**, because the library holds no example: `failed_and_no_audio.json` (a failed
track and one with no audio-only stream) and `from_a_later_version.json` (keys this version has
never heard of, for the forward-compatibility case).
