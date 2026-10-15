# SPDX-License-Identifier: GPL-3.0-or-later
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp>=1.2,<2",
#   "numpy>=1.26",
#   "scipy>=1.11",
#   "soundfile>=0.12.1",
#   "pyloudnorm>=0.1.1",
#   "visqol-python[lattice]>=3.8",
# ]
# ///
"""
audio_fidelity_mcp — MCP server (stdio, headless) that compares a processed audio file
against a reference and returns deterministic metrics as JSON.

MCP client (the only setup):
  {"mcpServers": {"audio-fidelity": {"command": "uv",
     "args": ["run", "/path/to/audio_fidelity_mcp.py"]}}}

`uv` reads the dependency block above and installs everything on first run.
PEAQ (binary) and ViSQOL (pip package) ship with the package: nothing to compile.

What is what (methodological honesty)
  * `peaq`: PEAQ Basic (ITU-R BS.1387-1) via peaqb (G. Gottardi, GPLv2). The real ODG,
    but NOT validated here against the ITU conformance vectors.
  * `visqol`: ViSQOL (Google) via visqol-python 3.8 (Apache-2.0), MOS-LQO 1–5. Pure
    Python implementation of ViSQOL v3 with the same TFLite lattice mapping as the C++
    default. Parity checked against the official C++ (speequal): identical files give
    4.7266 / 4.7321 on both; MP3-24k gives 3.2048 on both. Use `speech` mode (16 kHz)
    for VOICE; `audio` mode (48 kHz) for music. MOS-LQO: 5 perfect … 1 bad.
  * `polqa`: ITU-T P.863 is PROPRIETARY and cannot be embedded. The server only talks
    to a binary you licensed, via POLQA_BIN (+ optional POLQA_ARGS). The default CLI is
    an UNVERIFIED assumption; raw output is always returned in `raw_output`.
  * `psychoacoustic_proxy`: own proxy (Schroeder spreading + Terhardt threshold).
    NOT an ODG and NOT calibrated against subjective tests.
  * Everything else (alignment, gain, SNR, residual, spectrum, bandwidth, loudness)
    is exact objective measurement.
"""
from __future__ import annotations

import contextlib
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from mcp.server.fastmcp import FastMCP
from scipy import signal

mcp = FastMCP("audio_fidelity_mcp")

# All tools are read-only measurements: no side effects, safe to call repeatedly.
_ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False}

EPS = 1e-12
BARK_EDGES = np.array([0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480,
                       1720, 2000, 2320, 2700, 3150, 3700, 4400, 5300, 6400, 7700,
                       9500, 12000, 15500], dtype=float)
NB = len(BARK_EDGES) - 1


# --------------------------------------------------------------------------- I/O
def _decode_ffmpeg(path: str) -> tuple[np.ndarray, int]:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ValueError(f"Format not supported by libsndfile and ffmpeg is not on PATH: {path}. "
                         "Fix: install ffmpeg, or provide WAV/FLAC/OGG/MP3.")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "x.wav")
        subprocess.run([ff, "-v", "error", "-y", "-i", path, "-c:a", "pcm_f32le", out],
                       check=True, capture_output=True, timeout=300)
        return _read(out)


def _read(path: str) -> tuple[np.ndarray, int]:
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    return x, sr


def _load(path: str) -> tuple[np.ndarray, int]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return _read(str(p))
    except Exception:
        return _decode_ffmpeg(str(p))


def _db(x: float) -> float:
    return float(10 * math.log10(max(x, EPS)))


def _r(v: Any, n: int = 3) -> Any:
    if isinstance(v, (float, np.floating)):
        return None if not math.isfinite(v) else round(float(v), n)
    if isinstance(v, dict):
        return {k: _r(x, n) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_r(x, n) for x in v]
    return v


# --------------------------------------------------------------------- alignment
def _estimate_delay(ref: np.ndarray, test: np.ndarray, sr: int,
                    max_lag_s: float, window_s: float) -> float:
    """Delay (samples, fractional) of `test` relative to `ref`. >0 = test is late.

    Robust to tonal/periodic signals: gathers peak candidates from the plain
    cross-correlation and from GCC-PHAT, picks the one that maximizes the normalized
    time-domain correlation (PHAT breaks periodic ties), then refines the fractional
    part by parabolic fit.
    """
    n = min(len(ref), len(test), int(window_s * sr))
    r = ref[:n].mean(axis=1)
    t = test[:n].mean(axis=1)
    if np.max(np.abs(r)) < 1e-9 or np.max(np.abs(t)) < 1e-9:
        return 0.0
    m = int(max_lag_s * sr)
    nfft = int(2 ** math.ceil(math.log2(2 * n)))
    R, T = np.fft.rfft(r, nfft), np.fft.rfft(t, nfft)
    cross = T * np.conj(R)
    plain = np.fft.irfft(cross, nfft)
    phat = np.fft.irfft(cross / (np.abs(cross) + 1e-9 * np.abs(cross).max()), nfft)

    def at(c, lags):  # circular index -> lag
        return c[np.mod(lags, nfft)]

    lags = np.arange(-m, m + 1)
    cands: set[int] = set()
    for c in (plain, phat):
        v = at(c, lags)
        pk, _ = signal.find_peaks(v, distance=2)
        top = pk[np.argsort(v[pk])[::-1][:8]] if len(pk) else np.array([int(np.argmax(v))])
        cands.update(int(lags[i]) for i in top)

    def rho(L: int) -> float:
        a, b = (r[:n - L], t[L:n]) if L >= 0 else (r[-L:n], t[:n + L])
        if len(a) < 1024:
            return -1.0
        return float(np.dot(a, b) / (np.sqrt(np.dot(a, a) * np.dot(b, b)) + EPS))

    # rho discrimina true alignment; PHAT only breaks ties between periodic echoes.
    best = max(cands, key=lambda L: (round(rho(L), 6), float(phat[L % nfft])))
    # fractional refinement: minimizes residual (LS gain) on a mid window
    seg = slice(max(0, n // 2 - 3 * sr), min(n, n // 2 + 3 * sr))
    rr = ref[:n][seg]
    tt = test[:n]

    def cost(d: float) -> float:
        y = _shift(tt, best + d)[seg]
        k = min(len(y), len(rr))
        a, b = rr[:k], y[:k]
        g = float(np.sum(a * b) / (np.sum(b * b) + EPS))
        return float(np.sum((a - g * b) ** 2) / (np.sum(a * a) + EPS))

    from scipy.optimize import minimize_scalar
    res = minimize_scalar(cost, bounds=(-0.5, 0.5), method="bounded",
                          options={"xatol": 1e-4})
    return float(best + res.x)


def _shift(x: np.ndarray, delay: float) -> np.ndarray:
    """Removes `delay` samples (fractional) from x, keeping the length."""
    k = int(math.floor(delay))
    frac = delay - k
    if k >= 0:
        y = np.vstack([x[k:], np.zeros((k, x.shape[1]))])
    else:
        y = np.vstack([np.zeros((-k, x.shape[1])), x[:k]])
    if abs(frac) > 1e-3:
        N = len(y)
        nfft = int(2 ** math.ceil(math.log2(N)))
        Y = np.fft.rfft(y, nfft, axis=0)
        f = np.fft.rfftfreq(nfft)[:, None]
        y = np.fft.irfft(Y * np.exp(2j * np.pi * f * frac), nfft, axis=0)[:N]
    return y


# ------------------------------------------------------------------ psychoacoustics
def _bark(f: np.ndarray) -> np.ndarray:
    return 13 * np.arctan(0.00076 * f) + 3.5 * np.arctan((f / 7500) ** 2)


def _spread_matrix() -> np.ndarray:
    z = _bark((BARK_EDGES[:-1] + BARK_EDGES[1:]) / 2)
    dz = z[None, :] - z[:, None]          # [masked i, masker j]
    s_db = 15.81 + 7.5 * (dz + 0.474) - 17.5 * np.sqrt(1 + (dz + 0.474) ** 2)
    return 10 ** (s_db / 10)


def _ath_db_spl(f_hz: np.ndarray) -> np.ndarray:
    k = np.clip(f_hz, 20, 20000) / 1000
    return 3.64 * k ** -0.8 - 6.5 * np.exp(-0.6 * (k - 3.3) ** 2) + 1e-3 * k ** 4


def _stft_pow(x: np.ndarray, sr: int, nfft: int = 2048, hop: int = 512):
    w = signal.get_window("hann", nfft)
    f, _, X = signal.stft(x, sr, window=w, nperseg=nfft, noverlap=nfft - hop,
                          boundary=None, padded=False, return_onesided=True)
    P = (np.abs(X) ** 2) * (np.sum(w) ** 2)          # undo scipy normalization
    P = P * 2 / (nfft * np.sum(w ** 2)) * 2           # 0 dB = full-scale sine
    return f, P                                       # P: [bins, frames]


def _band_pow(P: np.ndarray, f: np.ndarray, sr: int) -> np.ndarray:
    out = np.zeros((NB, P.shape[1]))
    for i in range(NB):
        lo, hi = BARK_EDGES[i], min(BARK_EDGES[i + 1], sr / 2)
        if lo >= sr / 2:
            out[i] = np.nan
            continue
        out[i] = P[(f >= lo) & (f < hi)].sum(axis=0)
    return out


def _bandwidth(x: np.ndarray, sr: int, drop_db: float = 70.0) -> float:
    f, p = signal.welch(x, sr, nperseg=4096)
    pdb = 10 * np.log10(p + EPS)
    ok = np.flatnonzero(pdb > pdb.max() - drop_db)
    return float(f[ok[-1]]) if len(ok) else 0.0


def _spectral_and_psy(ref: np.ndarray, test: np.ndarray, sr: int, spl_fs: float) -> dict:
    S = _spread_matrix()
    centers = (BARK_EDGES[:-1] + BARK_EDGES[1:]) / 2
    ath_pow = 10 ** ((_ath_db_spl(centers) - spl_fs) / 10)       # power rel. FS
    valid = BARK_EDGES[:-1] < sr / 2
    lvl, nmr_cells, aud_frac, band_nmr, lsd_all, band_snr = [], [], [], [], [], []
    for ch in range(ref.shape[1]):
        f, Pr = _stft_pow(ref[:, ch], sr)
        _, Pt = _stft_pow(test[:, ch], sr)
        _, Pe = _stft_pow(ref[:, ch] - test[:, ch], sr)
        Br, Bt, Be = (_band_pow(P, f, sr) for P in (Pr, Pt, Pe))
        act = Br > ath_pow[:, None] * 4                           # audible ref band
        d = 10 * np.log10((Bt + EPS) / (Br + EPS))
        lvl.append(np.array([np.nanmean(d[i][act[i]]) if act[i].any() else np.nan for i in range(NB)]))
        band_snr.append(10 * np.log10((Br.sum(1) + EPS) / (Be.sum(1) + EPS)))
        # LSD (dB) over bins with relevant ref energy
        m = Pr > 1e-10
        lsd_all.append(float(np.sqrt(np.mean((10 * np.log10((Pt[m] + EPS) / (Pr[m] + EPS))) ** 2)))
                       if m.any() else np.nan)
        # masking: spread ref excitation - 5.5 dB (noise-like masker), floor = ATH
        ex_r = S @ np.nan_to_num(Br)
        thr = np.maximum(ex_r * 10 ** (-5.5 / 10), ath_pow[:, None])
        ex_e = S @ np.nan_to_num(Be)
        nmr = 10 * np.log10((ex_e + EPS) / thr)
        cells = act & valid[:, None]
        nmr_cells.append(float(np.mean(nmr[cells])) if cells.any() else np.nan)
        aud_frac.append(float(np.mean(nmr[cells] > 0) * 100) if cells.any() else 0.0)
        band_nmr.append(np.array([np.mean(nmr[i][act[i]]) if act[i].any() else np.nan for i in range(NB)]))
    bn = np.nanmean(np.vstack(band_nmr), axis=0)
    bl = np.nanmean(np.vstack(lvl), axis=0)
    bs = np.nanmean(np.vstack(band_snr), axis=0)
    bands = [{"band": i + 1, "hz_range": f"{int(BARK_EDGES[i])}-{int(BARK_EDGES[i+1])}",
              "level_diff_db": bl[i], "snr_db": bs[i], "nmr_db": bn[i]}
             for i in range(NB) if valid[i]]
    worst = sorted([b for b in bands if b["nmr_db"] is not None and not math.isnan(b["nmr_db"])],
                   key=lambda b: -b["nmr_db"])[:3]
    return {
        "lsd_db": float(np.nanmean(lsd_all)),
        "psycho_total_nmr_db": float(np.nanmean(nmr_cells)),
        "psycho_audible_cells_pct": float(np.mean(aud_frac)),
        "psycho_worst_bands": worst,
        "bark_bands": bands,
    }


# ------------------------------------------------------------------ comparison core
def _verdict(ident: bool, pct: float, nmr: float, snr_db: float) -> str:
    if ident:
        return "IDENTICAL (numerical difference ≈ 0 after alignment)"
    if pct < 1 and nmr < -10:
        return "Probably transparent (error below the proxy's masking threshold)"
    if pct < 5:
        return "Subtle degradation (few audible time-frequency regions in the proxy)"
    if pct < 20:
        return "Audible degradation"
    return "Clear / strong degradation"


def _compare(ref_path: str, test_path: str, max_lag_s: float = 2.0,
             match_gain: bool = True, spl_fs: float = 96.0,
             include_bands: bool = True) -> dict:
    ref, sr = _load(ref_path)
    test, sr_t = _load(test_path)
    notes: list[str] = []

    if sr_t != sr:
        notes.append(f"Resampled test {sr_t}→{sr} Hz (polyphase); measurements above "
                     f"{min(sr, sr_t)//2} Hz may be affected.")
        g = math.gcd(sr, sr_t)
        test = signal.resample_poly(test, sr // g, sr_t // g, axis=0)
    if ref.shape[1] != test.shape[1]:
        notes.append(f"Different channel counts (ref={ref.shape[1]}, test={test.shape[1]}): "
                     "compared in mono (downmix).")
        ref, test = ref.mean(1, keepdims=True), test.mean(1, keepdims=True)

    dur_ref, dur_test = len(ref) / sr, len(test) / sr
    delay = _estimate_delay(ref, test, sr, max_lag_s, 30.0)
    test = _shift(test, delay)
    n = min(len(ref), len(test))
    trim = min(2048, n // 10)                       # trim edges (codec wrap/fade)
    ref, test = ref[trim:n - trim], test[trim:n - trim]
    if len(ref) < 4096:
        raise ValueError("Audio too short after alignment (<4096 samples).")

    er2, et2 = float(np.sum(ref ** 2)), float(np.sum(test ** 2))
    gopt = float(np.sum(ref * test) / (et2 + EPS))
    gain_db = -20 * math.log10(max(abs(gopt), EPS))  # >0: test louder than ref
    t_used = test * gopt if match_gain else test
    err_raw = ref - test
    err = ref - t_used

    snr_raw = _db(er2 / (np.sum(err_raw ** 2) + EPS))
    snr = _db(er2 / (np.sum(err ** 2) + EPS))
    fr = int(0.02 * sr)
    k = (len(ref) // fr) * fr
    R = (ref[:k].reshape(-1, fr, ref.shape[1]) ** 2).sum((1, 2))
    E = (err[:k].reshape(-1, fr, ref.shape[1]) ** 2).sum((1, 2))
    act = R > 1e-10 * fr  # noqa: SIM300 - threshold expression, not a Yoda comparison
    seg = np.clip(10 * np.log10((R[act] + EPS) / (E[act] + EPS)), -10, 35)
    max_abs = float(np.max(np.abs(err)))
    identical = max_abs < 1e-9

    spec = _spectral_and_psy(ref, t_used, sr, spl_fs)

    # loudness (BS.1770) — optional, silently skipped for clips <0.4 s
    lufs = {}
    try:
        import pyloudnorm as pyln
        m = pyln.Meter(sr)
        lufs = {"reference": m.integrated_loudness(ref), "test": m.integrated_loudness(test)}
        lufs["difference_lu"] = lufs["test"] - lufs["reference"]
    except Exception:
        pass

    out = {
        "files": {"reference": ref_path, "test": test_path},
        "format": {"sample_rate_hz": sr, "channels": int(ref.shape[1]),
                   "reference_duration_s": dur_ref, "test_duration_s": dur_test,
                   "samples_compared": int(len(ref))},
        "alignment": {"delay_samples": delay, "delay_ms": 1000 * delay / sr,
                      "optimal_gain_applied": match_gain,
                      "level_difference_db": gain_db},
        "time_domain": {"snr_db": snr, "snr_without_gain_compensation_db": snr_raw,
                        "segmental_snr_db": float(np.mean(seg)) if len(seg) else None,
                        "error_rms_dbfs": _db(float(np.mean(err ** 2))),
                        "error_peak_abs": max_abs,
                        "correlation": float(np.corrcoef(ref.ravel(), t_used.ravel())[0, 1])},
        "spectrum": {"lsd_db": spec["lsd_db"],
                     "reference_bandwidth_hz": _bandwidth(ref.mean(1), sr),
                     "test_bandwidth_hz": _bandwidth(test.mean(1), sr)},
        "signal": {"reference_peak": float(np.max(np.abs(ref))), "test_peak": float(np.max(np.abs(test))),
                   "reference_dc": float(ref.mean()), "test_dc": float(test.mean()),
                   "test_clipped_samples": int(np.sum(np.abs(test) >= 0.9999))},
        "loudness_lufs": lufs,
        "psychoacoustic_proxy": {"WARNING": "Own proxy, NOT an ODG and NOT subjectively calibrated.",
                                 "total_nmr_db": spec["psycho_total_nmr_db"],
                                 "audible_time_freq_cells_pct": spec["psycho_audible_cells_pct"],
                                 "worst_bands": spec["psycho_worst_bands"]},
        "verdict": _verdict(identical, spec["psycho_audible_cells_pct"],
                            spec["psycho_total_nmr_db"], snr),
        "notes": notes,
    }
    if include_bands:
        out["bark_bands"] = spec["bark_bands"]
    return _r(out)


# ------------------------------------------------------------------------ real PEAQ
# PEAQ Basic (ITU-R BS.1387-1) via `peaqb` (G. Gottardi, GPLv2), bundled in bin/ for
# linux/macos/windows x86_64+arm64. Executed as a separate process.
_HERE = Path(__file__).resolve().parent
_PEAQ_CACHE: dict[str, Any] = {}


def _platform_tag() -> str:
    import platform
    sysname = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(platform.system(), "")
    mach = platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "x86_64" if mach in ("x86_64", "amd64") else mach
    if sysname == "linux" and arch == "arm64":
        arch = "aarch64"
    return f"{sysname}-{arch}"


def _smoke(exe: str) -> bool:
    try:
        p = subprocess.run([exe, "-h"], capture_output=True, text=True, timeout=15)
        return "usage" in (p.stdout + p.stderr).lower()
    except Exception:
        return False


def _peaq_binary() -> tuple[str | None, str]:
    """Returns (executable_path, source|failure_reason). Result is cached."""
    if "bin" in _PEAQ_CACHE:
        return _PEAQ_CACHE["bin"]
    res: tuple[str | None, str]
    env = os.environ.get("PEAQ_BIN")
    if env:
        res = (env, "PEAQ_BIN") if _smoke(env) else (None, f"PEAQ_BIN={env} does not run")
    else:
        tag = _platform_tag()
        src = _HERE / "bin" / f"peaqb-{tag}{'.exe' if tag.startswith('windows') else ''}"
        if not src.is_file():
            res = (None, f"no bundled binary for platform '{tag}' (see bin/ and build_binaries.py)")
        else:
            # zip/volumes lose the exec bit -> copy to cache with chmod +x
            cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "audio-fidelity-mcp"
            try:
                cache.mkdir(parents=True, exist_ok=True)
            except Exception:
                cache = Path(tempfile.gettempdir()) / "audio-fidelity-mcp"
                cache.mkdir(parents=True, exist_ok=True)
            dst = cache / src.name
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copyfile(src, dst)
            with contextlib.suppress(Exception):
                dst.chmod(0o755)
            res = (str(dst), f"bundled ({tag})") if _smoke(str(dst)) else \
                  (None, f"bundled binary ({tag}) does not run on this system (noexec? antivirus?)")
    _PEAQ_CACHE["bin"] = res
    return res


_MOV_KEYS = ["BandwidthRefb", "BandwidthTestb", "TotalNMRb", "WinModDiff1b", "ADBb", "EHSb",
             "AvgModDiff1b", "AvgModDiff2b", "RmsNoiseLoudb", "MFPDb", "RelDistFramesb"]


def _odg_label(odg: float) -> str:
    if odg > -0.5:
        return "imperceptible (ODG ≈ 0)"
    if odg > -1.5:
        return "perceptible but not annoying (≈ -1)"
    if odg > -2.5:
        return "slightly annoying (≈ -2)"
    if odg > -3.5:
        return "annoying (≈ -3)"
    return "very annoying (≈ -4)"


def _peaq(ref_path: str, test_path: str, playback_level_db: int = 92) -> dict:
    exe, source = _peaq_binary()
    if not exe:
        return {"available": False, "odg": None, "reason": source}
    ref, sr = _load(ref_path)
    test, sr_t = _load(test_path)
    notes: list[str] = []
    if ref.shape[1] != test.shape[1] or ref.shape[1] > 2:
        notes.append("Different or >2 channels: PEAQ computed in mono (downmix).")
        ref, test = ref.mean(1, keepdims=True), test.mean(1, keepdims=True)

    def to48(x: np.ndarray, s: int) -> np.ndarray:  # BS.1387 requires 48 kHz
        if s == 48000:
            return x
        g = math.gcd(48000, s)
        return signal.resample_poly(x, 48000 // g, s // g, axis=0)

    if sr != 48000 or sr_t != 48000:
        notes.append(f"Resampled to 48 kHz (ref {sr}, test {sr_t}) as required by BS.1387.")
    ref, test = to48(ref, sr), to48(test, sr_t)
    delay = _estimate_delay(ref, test, 48000, 2.0, 30.0)
    test = _shift(test, delay)
    n = min(len(ref), len(test))
    trim = min(2048, n // 10)
    ref, test = ref[trim:n - trim], test[trim:n - trim]
    if len(ref) < 48000 // 2:
        return {"available": True, "odg": None, "error": "Audio too short for PEAQ (<0.5 s)."}
    if max(np.max(np.abs(ref)), np.max(np.abs(test))) > 1.0:
        notes.append("Amplitude >1.0 (float): clipped to ±1 for PCM16 (may change the ODG).")
    lp = f":{int(playback_level_db)}" if int(playback_level_db) != 92 else ""
    with tempfile.TemporaryDirectory() as td:
        # relative names + cwd=td: avoids ':' in Windows paths (peaqb uses ':' for level)
        sf.write(os.path.join(td, "ref.wav"), np.clip(ref, -1, 1), 48000, subtype="PCM_16")
        sf.write(os.path.join(td, "test.wav"), np.clip(test, -1, 1), 48000, subtype="PCM_16")
        try:
            p = subprocess.run([exe, "-r", "ref.wav" + lp, "-t", "test.wav" + lp], cwd=td,
                               capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return {"available": True, "odg": None, "error": "timeout (file too long?)"}
    txt = p.stdout + "\n" + p.stderr

    def last(name: str) -> float | None:  # peaqb prints cumulative per-frame values
        m = re.findall(rf"^\s*{re.escape(name)}\s*:?\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$",
                       txt, flags=re.M)
        return float(m[-1]) if m else None

    odg, di = last("ODG"), last("DI")
    if odg is None:
        return {"available": True, "odg": None, "error": txt.strip()[-600:]}
    return _r({"available": True, "algorithm": "PEAQ Basic (ITU-R BS.1387-1), peaqb/Gottardi",
               "backend": source, "odg": odg, "odg_interpretation": _odg_label(odg),
               "distortion_index": di, "playback_level_db_spl": playback_level_db,
               "movs": {k: last(k) for k in _MOV_KEYS},
               "preprocessing": f"48 kHz/PCM16, aligned {delay:.3f} samples, edges trimmed",
               "notes": notes}, 4)


# ------------------------------------------------------------------------ ViSQOL
# ViSQOL v3 (Google, Apache-2.0) for VOICE and music: MOS-LQO 1–5. Backend: visqol-python
# (pure Python, same TFLite lattice mapping as the C++). Parity checked against the
# official C++ (speequal): identical 4.7266/4.7321; MP3-24k 3.2048.
_VISQOL_CACHE: dict[str, Any] = {}


def _visqol_api(mode: str):
    key = f"api:{mode}"
    if key in _VISQOL_CACHE:
        return _VISQOL_CACHE[key]
    try:
        from visqol import VisqolApi
        api = VisqolApi()
        api.create(mode=mode)          # use_lattice_model defaults to True (C++ default)
        _VISQOL_CACHE[key] = (api, None)
    except Exception as e:             # import failure, missing model, broken litert…
        _VISQOL_CACHE[key] = (None, f"{type(e).__name__}: {e}")
    return _VISQOL_CACHE[key]


def _mos_label(mos: float) -> str:
    if mos >= 4.5:
        return "transparent (lattice mapping ceiling ≈ 4.73)"
    if mos >= 4.0:
        return "very high quality"
    if mos >= 3.5:
        return "good"
    if mos >= 3.0:
        return "fair"
    if mos >= 2.0:
        return "poor"
    return "bad"


def _visqol(ref_path: str, test_path: str, mode: str = "auto") -> dict:
    """MOS-LQO from ViSQOL. mode: 'speech' (voice, 16 kHz), 'audio' (music, 48 kHz) or
    'auto' (audio if the reference is 48 kHz, otherwise speech)."""
    try:
        import visqol as _vq
        version = getattr(_vq, "__version__", "?")
    except Exception as e:
        return {"available": False, "moslqo": None,
                "reason": f"visqol-python not importable ({type(e).__name__}: {e})"}
    ref, sr = _load(ref_path)
    test, sr_t = _load(test_path)
    if mode == "auto":
        mode = "audio" if sr == 48000 else "speech"
    if mode not in ("speech", "audio"):
        return {"available": False, "moslqo": None, "reason": f"invalid mode: {mode}"}
    target = 16000 if mode == "speech" else 48000
    notes: list[str] = []
    if ref.shape[1] > 1 or test.shape[1] > 1:
        notes.append("Downmixed to mono (ViSQOL measures one channel).")
        ref, test = ref.mean(axis=1, keepdims=True), test.mean(axis=1, keepdims=True)
    if sr != target or sr_t != target:
        notes.append(f"Resampled to {target} Hz (ref {sr}, test {sr_t}) as required by {mode} mode.")
        def to(x, s):
            if s == target:
                return x
            g = math.gcd(target, s)
            return signal.resample_poly(x, target // g, s // g, axis=0)
        ref, test = to(ref, sr), to(test, sr_t)
    n = min(len(ref), len(test))
    dur = n / target
    ref, test = np.clip(ref[:n, 0], -1, 1), np.clip(test[:n, 0], -1, 1)
    if dur < 0.5:
        return {"available": True, "moslqo": None,
                "error": f"Audio too short for ViSQOL ({dur:.2f} s < 0.5 s)."}
    if dur < 1.0:
        notes.append("Duration <1 s: ViSQOL was calibrated on longer clips; score less reliable.")
    api, err = _visqol_api(mode)
    if api is None:
        return {"available": False, "moslqo": None, "reason": f"ViSQOL backend failed: {err}"}
    try:
        r = api.measure_from_arrays(ref.astype(np.float64), test.astype(np.float64), target)
    except Exception as e:
        return {"available": True, "moslqo": None, "error": f"{type(e).__name__}: {e}"}
    return _r({"available": True, "backend": f"visqol-python {version} (Apache-2.0, Google ViSQOL v3)",
               "mode": mode, "sample_rate_hz": target,
               "moslqo": float(r.moslqo), "moslqo_interpretation": _mos_label(float(r.moslqo)),
               "vnsim": float(r.vnsim),
               "fvnsim10_mean": float(np.mean(r.fvnsim10)),
               "fstdnsim_mean": float(np.mean(r.fstdnsim)),
               "scale": "MOS-LQO: 1 = bad … 5 = perfect; identical ≈ 4.73 (lattice ceiling)",
               "notes": notes}, 4)


# ------------------------------------------------------------------------ POLQA
# ITU-T P.863 (POLQA) is PROPRIETARY: there is no open implementation to embed.
# This wrapper only talks to a POLQA binary that the user has licensed.
#   POLQA_BIN=/path/to/Polqa        executable (required)
#   POLQA_ARGS="-t {ref} -d {deg} -o {out}"
#                                 argument template (placeholders: {ref} {deg} {out});
#                                 the default below is an ASSUMPTION — the official CLI
#                                 was not verified here (no licensed binary available).
# Raw output (stdout+stderr) always comes back in `raw_output` for you to check/adjust.
_POLQA_CACHE: dict[str, Any] = {}
_DEFAULT_POLQA_ARGS = "-t {ref} -d {deg} -o {out}"


def _polqa_binary() -> tuple[str | None, str]:
    if "bin" in _POLQA_CACHE:
        return _POLQA_CACHE["bin"]
    env = os.environ.get("POLQA_BIN")
    if not env:
        res = (None, "POLQA_BIN not set (POLQA is proprietary: point it at your licensed "
                     "binary in the MCP client's `env`)")
    elif not os.path.isfile(env):
        res = (None, f"POLQA_BIN={env} is not a file")
    else:
        res = (env, "POLQA_BIN")
    _POLQA_CACHE["bin"] = res
    return res


def _polqa(ref_path: str, test_path: str) -> dict:
    """MOS-LQO from POLQA (ITU-T P.863) via an external licensed binary (POLQA_BIN)."""
    exe, source = _polqa_binary()
    if not exe:
        return {"available": False, "moslqo": None, "reason": source}
    args_tpl = os.environ.get("POLQA_ARGS", _DEFAULT_POLQA_ARGS)
    ref, sr = _load(ref_path)
    test, sr_t = _load(test_path)
    notes: list[str] = []
    if ref.shape[1] > 1 or test.shape[1] > 1:
        notes.append("Downmixed to mono (POLQA measures one channel).")
        ref, test = ref.mean(axis=1, keepdims=True), test.mean(axis=1, keepdims=True)
    if sr != 48000 or sr_t != 48000:
        notes.append(f"Resampled to 48 kHz (ref {sr}, test {sr_t}).")
        def to48(x, s):
            if s == 48000:
                return x
            g = math.gcd(48000, s)
            return signal.resample_poly(x, 48000 // g, s // g, axis=0)
        ref, test = to48(ref, sr), to48(test, sr_t)
    delay = _estimate_delay(ref, test, 48000, 2.0, 30.0)
    test = _shift(test, delay)
    n = min(len(ref), len(test))
    trim = min(2048, n // 10)
    ref, test = ref[trim:n - trim], test[trim:n - trim]
    if len(ref) < 48000 // 2:
        return {"available": True, "moslqo": None, "error": "Audio too short for POLQA (<0.5 s)."}
    with tempfile.TemporaryDirectory() as td:
        rp, dp, op = (os.path.join(td, x) for x in ("ref.wav", "deg.wav", "out.txt"))
        sf.write(rp, np.clip(ref, -1, 1), 48000, subtype="PCM_16")
        sf.write(dp, np.clip(test, -1, 1), 48000, subtype="PCM_16")
        cmd = [exe] + args_tpl.format(ref=rp, deg=dp, out=op).split()
        try:
            p = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return {"available": True, "moslqo": None, "error": "timeout (file too long?)"}
        out_txt = ""
        out_file = Path(op)
        if out_file.is_file():                       # binaries that write a result file
            with contextlib.suppress(Exception):
                out_txt = out_file.read_text(errors="replace")
    txt = p.stdout + "\n" + p.stderr + ("\n" + out_txt if out_txt else "")

    def find(name: str) -> float | None:
        m = re.findall(rf"{name}[^\d\-]{{0,12}}(-?\d+(?:\.\d+)?)", txt, flags=re.I)
        vals = [float(x) for x in m if 1.0 - 1e-6 <= float(x) <= 5.0 + 1e-6]
        return vals[-1] if vals else None

    moslqo = find(r"MOS[- ]?LQO") or find(r"\bLQO\b") or find(r"POLQA[^\n]{0,20}score")
    res = _r({"available": True, "backend": f"external binary ({source})",
              "algorithm": "POLQA (ITU-T P.863) — NOT validated against ITU conformance vectors",
              "cli_used": cmd, "returncode": p.returncode,
              "moslqo": moslqo,
              "scale": "MOS-LQO: 1 = bad … 5 = perfect",
              "notes": notes + ([] if moslqo is not None else
                                ["MOS-LQO not extracted: check `raw_output` and adjust "
                                 "POLQA_ARGS to your binary's CLI."])}, 4)
    res["raw_output"] = txt.strip()[-1500:]
    return res


# ---------------------------------------------------------------------------- tools
@mcp.tool(annotations=_ANNOTATIONS)
def compare_audio(reference_path: str, test_path: str, max_lag_s: float = 2.0,
                  match_gain: bool = True, playback_spl_dbfs: float = 96.0,
                  include_bands: bool = True, run_peaq: bool = True,
                  visqol_mode: str = "auto") -> dict:
    """Compares `test_path` against `reference_path` (local paths; WAV/FLAC/OGG/MP3, and
    anything ffmpeg can decode, if installed).

    Performs sub-sample time alignment, estimates the optimal gain and measures:
    SNR/segmental SNR/residual, correlation, spectral distance (LSD), bandwidth,
    BS.1770 loudness, clipping, a psychoacoustic proxy (NMR per Bark band), PEAQ Basic
    (ODG, DI, MOVs) and ViSQOL (MOS-LQO 1–5).
    ODG: 0 = imperceptible, -4 = very annoying. MOS-LQO: 5 = perfect, 1 = bad.
    visqol_mode: 'speech' (voice, 16 kHz), 'audio' (music, 48 kHz) or 'auto' (audio only
    if the reference is 48 kHz; otherwise speech). Use 'none' to skip ViSQOL.
    """
    res = _compare(reference_path, test_path, max_lag_s, match_gain, playback_spl_dbfs, include_bands)
    res["peaq"] = _peaq(reference_path, test_path) if run_peaq else {"available": None, "odg": None}
    res["visqol"] = (_visqol(reference_path, test_path, visqol_mode)
                     if visqol_mode != "none" else {"available": None, "moslqo": None})
    return res


@mcp.tool(annotations=_ANNOTATIONS)
def compare_batch(reference_path: str, test_paths: list[str], match_gain: bool = True) -> dict:
    """Compares several test files against a single reference and returns a ranking
    (best→worst) by ODG (PEAQ), with summarized SNR/LSD/MOS-LQO. Use to compare codecs/bitrates."""
    rows = []
    for tp in test_paths:
        try:
            r = _compare(reference_path, tp, match_gain=match_gain, include_bands=False)
            pq = _peaq(reference_path, tp)
            vq = _visqol(reference_path, tp)
            rows.append({"test": tp,
                         "audible_pct": r["psychoacoustic_proxy"]["audible_time_freq_cells_pct"],
                         "nmr_db": r["psychoacoustic_proxy"]["total_nmr_db"],
                         "snr_db": r["time_domain"]["snr_db"],
                         "lsd_db": r["spectrum"]["lsd_db"],
                         "test_bandwidth_hz": r["spectrum"]["test_bandwidth_hz"],
                         "odg": pq.get("odg"), "moslqo": vq.get("moslqo"),
                         "verdict": r["verdict"]})
        except Exception as e:  # one bad file does not take down the batch
            rows.append({"test": tp, "error": str(e)})
    ok = [x for x in rows if "error" not in x]
    ok.sort(key=lambda x: (x["odg"] is None, -(x["odg"] or 0), x["audible_pct"], x["nmr_db"]))
    return {"reference": reference_path, "ranking": ok, "failures": [x for x in rows if "error" in x]}


@mcp.tool(annotations=_ANNOTATIONS)
def peaq_odg(reference_path: str, test_path: str, playback_level_db: int = 92) -> dict:
    """Returns ONLY PEAQ (ITU-R BS.1387-1 Basic): ODG (0 = imperceptible … -4 = very
    annoying), Distortion Index and the 11 MOVs. Handles 48 kHz resample and alignment.
    playback_level_db: assumed listening level (standard default: 92 dB SPL)."""
    return _peaq(reference_path, test_path, playback_level_db)


@mcp.tool(annotations=_ANNOTATIONS)
def visqol_score(reference_path: str, test_path: str, mode: str = "auto") -> dict:
    """Returns ONLY ViSQOL (Google, MOS-LQO 1–5, where 5 = perfect and 1 = bad) and the
    VNSIM (neurogram similarity, 0–1). Handles mono downmix and resampling.
    mode: 'speech' for VOICE (16 kHz, the mode calibrated for speech) or 'audio' for music
    (48 kHz); 'auto' picks based on the reference sample rate."""
    return _visqol(reference_path, test_path, mode)


@mcp.tool(annotations=_ANNOTATIONS)
def polqa_score(reference_path: str, test_path: str) -> dict:
    """Returns ONLY POLQA (ITU-T P.863): MOS-LQO 1–5 (5 = perfect, 1 = bad).

    NOTE: POLQA is proprietary and NOT bundled. Requires a licensed binary pointed to by
    `POLQA_BIN` (MCP client env). `POLQA_ARGS` (optional) sets the CLI with placeholders
    {ref} {deg} {out}; the default is an unverified assumption. Handles mono downmix,
    48 kHz resampling and alignment. Raw binary output comes back in `raw_output`.
    """
    return _polqa(reference_path, test_path)


@mcp.tool(annotations=_ANNOTATIONS)
def server_info() -> dict:
    """Reports which backends/capabilities are available on this server."""
    exe, source = _peaq_binary()
    try:
        import visqol as _vq
        vq = {"available": True, "version": getattr(_vq, "__version__", "?"),
              "modes": ["speech (16 kHz)", "audio (48 kHz)"],
              "backend": "visqol-python (Apache-2.0), TFLite lattice mapping as in the official C++"}
    except Exception as e:
        vq = {"available": False, "reason": str(e)}
    pqe, pqo = _polqa_binary()
    return {"peaq_basic_bs1387": bool(exe), "peaq_backend": source, "visqol": vq,
            "polqa_p863": {"available": bool(pqe), "reason": pqo,
                           "note": "proprietary: only runs with POLQA_BIN (your own license)"},
            "platform": _platform_tag(),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "native_formats": sorted(sf.available_formats().keys()),
            "note": "PEAQ Basic bundled (peaqb, GPLv2) + ViSQOL v3 (visqol-python, Apache-2.0). "
                    "POLQA (P.863) only via an external licensed binary."}


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
