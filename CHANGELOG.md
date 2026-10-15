# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-15

### Added
- `compare_audio`: sub-sample alignment + optimal gain, SNR/segmental SNR/residual,
  correlation, LSD, bandwidth, BS.1770 loudness, clipping, Bark-band psychoacoustic
  proxy, PEAQ Basic (ODG/DI/MOVs) and ViSQOL (MOS-LQO/VNSIM).
- `compare_batch`: ranks codecs/bitrates against one reference by ODG.
- `peaq_odg`: PEAQ Basic only (ITU-R BS.1387-1).
- `visqol_score`: ViSQOL v3 only — `speech` (16 kHz, voice) and `audio` (48 kHz, music) modes.
- `polqa_score`: wrapper for a licensed POLQA binary (`POLQA_BIN`).
- `server_info`: backend/platform diagnostics.
- Bundled peaqb binaries for 6 platforms (linux/macos/windows × x86_64/arm64).
