#!/usr/bin/env python3
"""Windows 版（Python 同梱）の zip と、自動更新用の app.zip を作る。

Linux / Mac / Windows どこでも実行できます（Windows 用のファイルをダウンロードして組み立てるだけ）。

    python tools/build_windows.py
    → dist/FaceCircleTool_Windows.zip       （初回配布用・Python 込み）
    → dist/app.zip                          （自動更新用・app フォルダの中身だけ）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build"
PY_VERSION = "3.12"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/20250409/"
           "cpython-3.12.10+20250409-x86_64-pc-windows-msvc-install_only.tar.gz")
PACKAGES = ["opencv-python-headless>=4.8,<5", "numpy", "tkinterdnd2"]
TOP = "顔丸切り抜きツール"

# 配布物に不要なもの（サイズ削減）
PRUNE = ["include", "libs", "Scripts", "Lib/test", "Lib/idlelib/idle_test", "Lib/ensurepip",
         "tcl/tix8.4.3"]


def build_app_zip() -> Path:
    out = DIST / "app.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((ROOT / "app").iterdir()):
            if p.is_file() and p.name != "error_log.txt":
                z.write(p, p.name)
    return out


def build_full_zip() -> Path:
    if WORK.exists():
        shutil.rmtree(WORK)
    base = WORK / TOP
    base.mkdir(parents=True)

    print("Python をダウンロード中…")
    tgz = WORK / "python.tar.gz"
    urllib.request.urlretrieve(PBS_URL, tgz)
    with tarfile.open(tgz) as t:
        t.extractall(base)
    py = base / "python"

    print("ライブラリ（Windows 用）をダウンロード中…")
    wheels = WORK / "wheels"
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                    "--platform", "win_amd64", "--python-version", PY_VERSION,
                    "-d", str(wheels), *PACKAGES], check=True)
    site = py / "Lib" / "site-packages"
    for whl in wheels.glob("*.whl"):
        with zipfile.ZipFile(whl) as z:
            z.extractall(site)

    for rel in PRUNE:
        shutil.rmtree(py / rel, ignore_errors=True)
    shutil.rmtree(site / "tkinterdnd2" / "__pyinstaller", ignore_errors=True)
    for d in (site / "tkinterdnd2" / "tkdnd").iterdir():
        if d.name != "win-x64":
            shutil.rmtree(d)
    for p in list(py.rglob("*.pdb")) + list((site / "cv2").glob("opencv_videoio_ffmpeg*.dll")):
        p.unlink()
    for p in list(site.glob("numpy/**/tests")):
        shutil.rmtree(p, ignore_errors=True)
    for p in list(py.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)

    shutil.copytree(ROOT / "app", base / "app",
                    ignore=shutil.ignore_patterns("__pycache__", "error_log.txt"))
    shutil.copy2(ROOT / "tools" / "launcher.bat", base / f"{TOP}.bat")
    shutil.copy2(ROOT / "tools" / "はじめにお読みください.txt", base / "はじめにお読みください.txt")

    out = DIST / "FaceCircleTool_Windows.zip"  # GitHub は日本語のファイル名を消すので英字にする
    print("zip を作成中…")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(base.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(WORK).as_posix())
    shutil.rmtree(WORK)
    return out


def main() -> None:
    DIST.mkdir(exist_ok=True)
    print(build_app_zip())
    if "--app-only" not in sys.argv:
        print(build_full_zip())


if __name__ == "__main__":
    main()
