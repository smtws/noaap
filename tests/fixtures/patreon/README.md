# Recorded Patreon answers

**None of these came from a real account, and none may.** They are written by hand in the *shape*
yt-dlp's `patreon` extractor returns (yt-dlp 2026.08.19, `yt_dlp/extractor/patreon.py`), with every
name, id and address invented. A real recording would carry a patron's session in its URLs, and one
committed cookie is one too many — `test_patreon.py::test_no_fixture_carries_a_session` greps this
whole directory for that, and it is not a formality: it is the only thing standing between a live run
and this repository.

What each file is:

| file | the case it exists for |
|---|---|
| `post_one_audio.json` | the ordinary post: one audio file, the post's own title |
| `post_three_attachments.json` | one post, three files — three tracks, in the post's order, named by their own file names |
| `post_locked.json` | a post the tier does not include: `current_user_can_view: false` and no formats |
| `post_video.json` | a video post, which this provider does not take |
| `post_embed_youtube.json` | a post that is a YouTube link with a note: the audio is YouTube's |
| `post_embed_unknown.json` | a post embedding a service noaap has no provider for |
| `campaign_own_posts.json` | a campaign's own posts, newest first, **with** per-entry titles and ids |
| `campaign_flat_bare.json` | what a flat listing of a live campaign really returned: `url` + `ie_key`, nothing else |
| `campaign_with_a_foreign_post.json` | the same listing with **a post of another campaign** in it, which is the hazard of yt-dlp [#10013](https://github.com/yt-dlp/yt-dlp/issues/10013) and must be dropped |

yt-dlp pages the feed itself, so these are one flat listing each rather than two pages: the cap is
asked for with `playlistend`, not applied after reading everything.

## Corrected by hand on 2026-09-29, after the first live run (§9, slice 71)

The run read one real campaign and five of its posts. Nothing recorded from it is in this directory
— what it taught was written into the files above by hand, anonymised:

- **A flat campaign listing carries only `{"_type": "url", "ie_key": "Patreon", "url": …}`.** No id,
  no title, no timestamp, no campaign id. `campaign_own_posts.json` had all four on every entry, so
  the picker looked like it had titles when in fact it printed five bare URLs. The measured shape is
  `campaign_flat_bare.json`; the richer one is kept because yt-dlp fills those fields when it reads a
  post rather than a feed, and `campaign_with_a_foreign_post.json` needs a per-entry campaign id to
  describe the hazard at all. **Note what that means:** against the bare shape the #10013 filter has
  nothing to compare and drops nothing — it guards a shape this campaign did not send.
- **A video post is an HLS manifest, not a file.** `post_video.json` said one direct URL with a
  duration and a filesize; the real answer is four `avc1`+`mp4a` Mux formats, `m3u8_native`, no
  duration, no filesize, English `vtt` subtitles, `has_drm: false` — and an `id` that is the **post's**
  own id, not a separate media id. All five posts read live had exactly this shape.
