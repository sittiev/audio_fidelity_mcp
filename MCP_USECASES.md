# MCP use cases

Prompts that work well with this server. Paste them to your agent after adding the
server to your MCP client (see README).

## Before publishing an audio file

> Did this 128kbps MP3 lose anything versus the master? Compare `/music/song.wav`
> against `/music/song_128.mp3` and tell me if the degradation is audible.

## Voice / VoIP / call recordings

> I re-encoded a customer support call for the archive. Is the transcription-ready
> version degraded? Compare `/calls/raw.wav` with `/calls/opus64.wav` using speech mode.

## Codec sweep

> Rank these exports of the same track by quality: `mix_128.mp3`, `mix_192.mp3`,
> `mix_256.mp3`, `mix_320.mp3`. Which is the best quality-per-bitrate tradeoff?

## Loudness / delivery specs

> The platform wants −14 LUFS integrated. Compare `master.wav` with `master_norm.wav`:
> did normalization clip anything, and did the ODG move?

## Meeting recording triage

> I have 30 recordings of the same session captured by different recorders. Find the
> ones that lost bandwidth or drifted in delay versus `ref_session.wav`.

## Regression check after editing

> I applied EQ and limiting to `podcast_ep12.wav`. Compare it with `podcast_ep12_before.wav`
> and flag anything above "slightly annoying" on the ODG.

## The agent will typically

1. call `server_info` to see which backends are available,
2. pick `visqol_score` (voice → `speech`, music → `audio`) or `peaq_odg`,
3. fall back to `compare_audio` when you ask for "everything",
4. use `compare_batch` when you hand it a list of files.
