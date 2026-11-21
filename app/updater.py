"""自動更新: GitHub の最新リリースを確認し、新しければアプリ部分（app フォルダ）を入れ替える。"""
from __future__ import annotations

import io
import json
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG = APP_DIR / "update_config.json"
VERSION_FILE = APP_DIR / "version.txt"
ASSET_NAME = "app.zip"  # リリースに添付する、app フォルダの中身を固めた zip


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def _vtuple(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV")
    out = []
    for part in v.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def _get(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "face-circle-tool-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return r.read()


def check() -> dict | None:
    """新しいバージョンがあれば {"version", "url", "notes"} を返す。なければ None。"""
    try:
        repo = json.loads(CONFIG.read_text(encoding="utf-8")).get("repo", "").strip()
    except (OSError, ValueError):
        return None
    if not repo:
        return None
    data = json.loads(_get(f"https://api.github.com/repos/{repo}/releases/latest"))
    latest = data.get("tag_name", "")
    if _vtuple(latest) <= _vtuple(current_version()):
        return None
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return {"version": latest.lstrip("vV"), "url": asset["browser_download_url"],
                    "notes": (data.get("body") or "").strip()}
    return None


def apply(info: dict) -> None:
    """app.zip を取ってきて app フォルダに上書きする（失敗したら元に戻す）。"""
    blob = _get(info["url"], timeout=60)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not any(n.endswith("gui.py") for n in names):
            raise RuntimeError("更新ファイルの中身が正しくありません")
        backup = Path(tempfile.mkdtemp(prefix="face_tool_backup_"))
        for p in APP_DIR.iterdir():
            if p.is_file():
                shutil.copy2(p, backup / p.name)
        try:
            for n in names:
                rel = Path(n)
                if rel.is_absolute() or ".." in rel.parts:
                    continue
                dst = APP_DIR / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(n))
            if "version.txt" not in [Path(n).name for n in names]:
                VERSION_FILE.write_text(info["version"], encoding="utf-8")
        except Exception:
            for p in backup.iterdir():
                shutil.copy2(p, APP_DIR / p.name)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)
