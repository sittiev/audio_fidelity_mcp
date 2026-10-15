<p align="center">
  <img src="https://www.freeiconspng.com/uploads/audio-sound-waves-png-22.png"
       alt="audio sound waves" width="140">
</p>

<p align="center">
  <a href="https://github.com/sittiev/audio_fidelity_mcp">
    <img src="https://img.shields.io/badge/Made%20with-Python3-brightgreen" alt="Made with Python3">
  </a>
  <a href="https://modelcontextprotocol.io">
    <img src="https://img.shields.io/badge/MCP-stdio%20headless-blue" alt="MCP stdio headless">
  </a>
  <a href="https://docs.astral.sh/uv/">
    <img src="https://img.shields.io/badge/zero--setup-uv-purple" alt="zero-setup uv">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="License GPL-3.0">
  </a>
</p>

<h1 align="center">audio_fidelity_mcp</h1>

<p align="center">
  MCP server that measures how much quality a processed audio file lost against its
  reference. PEAQ (ODG) and ViSQOL (MOS-LQO) are bundled. POLQA works if you own a licensed
  binary. Runs headless over stdio.
</p>

<p align="center">
  <a href="#what-it-does">What it does</a> •
  <a href="#setup">Setup</a> •
  <a href="#tools">Tools</a> •
  <a href="#usage">Usage</a> •
  <a href="#which-metric-to-use">Which metric</a> •
  <a href="#what-is-verified">What is verified</a> •
  <a href="#limits">Limits</a> •
  <a href="#credits">Credits</a>
</p>

## What it does

Give it two audio files. It aligns them, then reports what changed:

- Perceptual scores: PEAQ Basic ODG (ITU-R BS.1387-1) and ViSQOL MOS-LQO (Google, speech or
  audio mode). POLQA (ITU-T P.863) runs if you point it at your licensed binary.
- Waveform measurements: sub-sample delay, SNR, segmental SNR, residual peak, correlation,
  optimal gain, clipping, BS.1770 loudness.
- Spectral measurements: log-spectral distance, bandwidth of each file, and a
  noise-to-mask ratio per Bark band that points at the bands where damage concentrates.

Setup is one config block. `uv run` reads the dependency block inside the script and installs
everything on first run, so there is nothing to compile and nothing to download by hand.

## Setup

You need [`uv`](https://docs.astral.sh/uv/) on the machine. Nothing else.

Paste the prompt below into your agent (Claude Code, Codex, OpenCode, Cursor, Gemini CLI,
or any LLM with shell access). It clones the repo, registers the server, asks about POLQA, and
verifies that the backends came up.

<details open>
<summary><b>Setup prompt for your agent</b></summary>

```text
Install the MCP server "audio_fidelity_mcp" (measures audio quality loss: PEAQ ODG,
ViSQOL MOS-LQO, POLQA wrapper).

1. Run `uv --version`. If uv is missing, stop and link me to
   https://docs.astral.sh/uv/getting-started/installation/. Do not install anything else,
   do not use pip or virtualenvs.

2. Ask me where to clone (default ~/Projects/audio_fidelity_mcp), then:
   git clone https://github.com/sittiev/audio_fidelity_mcp.git <path>

3. Ask me: "Do you have a licensed POLQA binary? (y/n)". If no, skip to step 4.
   If yes, ask for its path and add to the server entry:
     "env": { "POLQA_BIN": "<path>", "POLQA_ARGS": "-t {ref} -d {deg} -o {out}" }
   Mention that the default POLQA_ARGS is an assumption we could not test, and that the tool
   returns the binary's raw output so we can fix the flags if it fails.

4. Register the server. Prefer the CLI:
   Claude Code: claude mcp add audio-fidelity -- uv run <path>/audio_fidelity_mcp.py
   Other clients: add this block to that client's MCP config, with an absolute path:
     { "command": "uv", "args": ["run", "<path>/audio_fidelity_mcp.py"] }

5. Call server_info() through the MCP client and report which backends are up (PEAQ, ViSQOL
   speech, ViSQOL audio, POLQA). Then run visqol_score() on a speech WAV I point you at and
   report the MOS-LQO.

Do not edit files inside the repo.
```

</details>

<details>
<summary><b>Doing it by hand</b></summary>

```bash
git clone https://github.com/sittiev/audio_fidelity_mcp.git
cd audio_fidelity_mcp
uv run audio_fidelity_mcp.py        # headless stdio, deps resolved on first run
```

Register it in your MCP client (keep the path absolute):

```json
{
  "mcpServers": {
    "audio-fidelity": {
      "command": "uv",
      "args": ["run", "/PATH/audio_fidelity_mcp/audio_fidelity_mcp.py"]
    }
  }
}
```

</details>

> [!IMPORTANT]
> **POLQA is proprietary.** OPTICOM and the ITU-T license the P.863 reference code and it
> cannot be redistributed, so this repository ships the `polqa_score` tool but no binary.
> Without `POLQA_BIN` the tool reports why and returns `null`. Voice is still measured, by
> ViSQOL speech mode at 16 kHz.
>
> If you own a binary, add it to the server entry:
>
> ```json
> "env": {
>   "POLQA_BIN": "/path/to/Polqa",
>   "POLQA_ARGS": "-t {ref} -d {deg} -o {out}"
> }
> ```
>
> `POLQA_ARGS` is optional. Placeholders: `{ref}` for the reference file, `{deg}` for the
> degraded file, `{out}` for the result file. The default assumes `-t`, `-d` and `-o`, which
> we could not verify without a licensed binary. If the flags are wrong the tool still returns
> the binary's own output under `raw_output`, so you can read its usage message and fix it.

## Tools

| Tool | Returns |
|---|---|
| `compare_audio(reference_path, test_path, visqol_mode="auto")` | Everything: ViSQOL, PEAQ, objective metrics, Bark bands |
| `compare_batch(reference_path, test_paths[])` | Ranking of codecs or bitrates by ODG, with MOS-LQO |
| `peaq_odg(reference_path, test_path, playback_level_db=92)` | PEAQ only: ODG, distortion index, 11 MOVs |
| `visqol_score(reference_path, test_path, mode="auto")` | ViSQOL only: MOS-LQO, VNSIM |
| `polqa_score(reference_path, test_path)` | POLQA only, needs `POLQA_BIN` |
| `server_info()` | Active backends, platform, formats, failure reasons |

ODG runs from 0 (imperceptible) to -4 (very annoying). MOS-LQO runs from 5 (perfect) to
1 (bad). Identical files give ODG near +0.2 and MOS-LQO near 4.73, which are the ceilings of
the two models, not a disagreement between them.

All tools take local paths. WAV, FLAC, OGG and MP3 are read natively; anything else works if
`ffmpeg` is installed. Downmix to mono, resampling and alignment are handled for you. Every
tool is marked `readOnly`, so calling one changes nothing.

## Usage

Ask your agent to compare two files:

> Compare `/music/master.wav` against `/music/song_128.mp3` and tell me if the degradation
> is audible.

This is what comes back, cut down to the interesting fields:

```jsonc
{
  "alignment":  { "delay_samples": -0.007, "delay_ms": 0.0 },
  "time_domain":{ "snr_db": 24.8, "segmental_snr_db": 15.1, "correlation": 0.97 },
  "spectrum":   { "lsd_db": 6.2, "reference_bandwidth_hz": 20500, "test_bandwidth_hz": 16000 },
  "psychoacoustic_proxy": { "audible_time_freq_cells_pct": 3.1, "total_nmr_db": -12.4 },
  "peaq":       { "odg": -1.42, "odg_interpretation": "perceptible but not annoying",
                  "distortion_index": 0.9 },
  "visqol":     { "mode": "audio", "moslqo": 3.9, "vnsim": 0.88 },
  "verdict":    "Subtle degradation (few audible time-frequency regions in the proxy)"
}
```

More prompts are in [MCP_USECASES.md](MCP_USECASES.md).

## Which metric to use

- Voice, speech and VoIP: `visqol_score(mode="speech")`. ViSQOL's speech mode was calibrated
  on speech and runs at 16 kHz. With a licensed binary, `polqa_score` is the telecom standard
  for calls.
- Music and audio codecs: `visqol_score(mode="audio")` at 48 kHz, and `peaq_odg` beside it.
- Long files: read the MOVs, not just the ODG. `Bandwidth`, `Noise-to-Mask` and `Average
  Modulation Difference` usually tell you where the damage is.
- IIR filters and anything else that distorts phase break time-domain SNR, because no single
  delay aligns every frequency. Trust the spectral numbers and the perceptual scores there.

## What is verified

| Part | Status |
|---|---|
| peaqb on linux-x86_64 | Ran in tests over the MCP protocol |
| peaqb on linux-aarch64, macos and windows builds | Compiled with `zig cc`, never ran here |
| ViSQOL speech and audio (visqol-python 3.8) | Ran in tests, checked against Google's C++ build: identical files scored 4.7266 and 4.7321 on both, MP3 at 24k scored 3.2048 on both |
| POLQA wrapper | Never ran. No licensed binary was available, and the default CLI flags are a guess |

If the bundled PEAQ binary refuses to run (a `noexec` mount, antivirus), the `server_info`
tool says why and `PEAQ_BIN` points it at another copy.

## Limits

> [!NOTE]
> No perceptual model here was checked against the ITU conformance vectors, which are paid
> documents. Numbers come from the tests listed above.

- PEAQ here is Basic, not Advanced. POLQA is a wrapper, and it needs your own licensed binary.
- ODG and MOS-LQO compare versions of the same material. They are not physical measurements,
  and they were not built to rank two different recordings against each other.

## Credits

- [peaqb](https://github.com/akinori-ito/peaqb-fast), PEAQ Basic by G. Gottardi, with Akinori
  Ito's speed patch, plus FFTW 2.x. Source and `COPYING` are in `vendor/`.
- [visqol-python](https://github.com/talker93/visqol-python), a pure-Python implementation of
  Google's [ViSQOL](https://github.com/google/visqol).
- [ai-edge-litert](https://pypi.org/project/ai-edge-litert/) runs the lattice model,
  [libsvm-official](https://pypi.org/project/libsvm-official/) runs the audio SVR.
- Structure follows the [mcp-builder](https://github.com/anthropics/skills) guide.

## License

GPL-3.0-or-later, because `vendor/peaqb` is GPLv2. See [LICENSE](LICENSE) and [NOTICE](NOTICE)
for third-party components and the changes made to them.