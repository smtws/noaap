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
| `campaign_page1.json`, `campaign_page2.json` | a campaign's posts, cursor-paged — and page 2 holds **a post of another campaign**, which is the hazard of yt-dlp #10013 and must be dropped |
