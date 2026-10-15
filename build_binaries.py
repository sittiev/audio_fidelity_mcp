#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["ziglang"]
# ///
"""Cross-compila o peaqb (vendor/peaqb) para todas as plataformas com `zig cc`.
Uso (só para mantenedores; usuários finais NÃO precisam rodar isto):
    uv run build_binaries.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
V = ROOT / "vendor" / "peaqb"
OUT = ROOT / "bin"
TARGETS = {  # zig target -> nome do arquivo
    "x86_64-linux-musl":   "peaqb-linux-x86_64",
    "aarch64-linux-musl":  "peaqb-linux-aarch64",
    "x86_64-macos":        "peaqb-macos-x86_64",
    "aarch64-macos":       "peaqb-macos-arm64",
    "x86_64-windows-gnu":  "peaqb-windows-x86_64.exe",
    "aarch64-windows-gnu": "peaqb-windows-arm64.exe",
}

def sources(windows: bool):
    src = sorted((V / "src").glob("*.c"))
    fftw = sorted(p for p in (V / "fftw").glob("*.c") if p.name != "fftwf77.c")
    extra = [V / "src/missing/getopt.c", V / "src/missing/getopt1.c"] if windows else []
    return [str(p) for p in src + fftw + extra]

def build(target: str, name: str) -> bool:
    win = "windows" in target
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", target, "-O2", "-w",
           "-DBASIC", "-DLITTLE", "-include", "string.h",
           # mingw não tem bzero; nos demais, usa o real (strings.h)
           *(["-Dbzero(p,n)=memset((p),0,(n))"] if win else ["-include", "strings.h"]),
           f"-I{V/'src/include'}", f"-I{V/'src/missing/include'}", f"-I{V/'fftw'}",
           *sources(win), "-o", str(OUT / name)]
    if not win:
        cmd.append("-lm")
    if "macos" not in target:
        cmd.append("-s")  # strip
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == 0
    print(("OK   " if ok else "FAIL ") + target, "" if ok else "\n" + r.stderr[-600:])
    return ok

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    res = {t: build(t, n) for t, n in TARGETS.items()}
    for pdb in OUT.glob("*.pdb"):
        pdb.unlink()
    sys.exit(0 if all(res.values()) else 1)
