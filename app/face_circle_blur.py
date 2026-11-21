#!/usr/bin/env python3
"""顔を自動検出し、顔を中心に丸く切り抜いて円の外側をぼかすツール。

使い方の例:
    python face_circle_blur.py photo.jpg                 # photo_circle.png を出力
    python face_circle_blur.py photo.jpg -o out.png      # 出力先を指定
    python face_circle_blur.py *.jpg --mode full         # 元のサイズのまま円の外をぼかす
    python face_circle_blur.py photo.jpg --mode transparent  # 円の外を透明にする
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


# ---------- 入出力（日本語パスでも動くように imdecode/imencode を使う） ----------
def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"画像を読み込めませんでした: {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def write_image(path: Path, img: np.ndarray) -> None:
    ext = path.suffix.lower() or ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, 95] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise ValueError(f"画像を書き出せませんでした: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


# ---------- 顔検出 ----------
CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml",
            "haarcascade_profileface.xml")
_cascade_cache: dict[str, "cv2.CascadeClassifier"] = {}


def load_cascade(name: str) -> "cv2.CascadeClassifier":
    """検出器を読み込む。

    Windows では OpenCV がフォルダ名に日本語などを含むパスを開けないため、
    ファイルを Python で読んでからメモリ経由で渡す。
    """
    if name not in _cascade_cache:
        xml = (Path(cv2.data.haarcascades) / name).read_text(encoding="utf-8")
        fs = cv2.FileStorage(xml, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
        cascade = cv2.CascadeClassifier()
        if not cascade.read(fs.getFirstTopLevelNode()) or cascade.empty():
            raise RuntimeError(f"顔検出器を読み込めませんでした: {name}")
        _cascade_cache[name] = cascade
    return _cascade_cache[name]


def detect_faces(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    """顔の矩形 (x, y, w, h) を確からしい順に返す。"""
    # 大きな写真は縮小して検出する（速く、安定する）
    k = min(1.0, 1200 / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else img
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    min_side = max(20, int(min(gray.shape[:2]) * 0.04))
    W = gray.shape[1]

    def run(name, neighbors, flip=False):
        g = cv2.flip(gray, 1) if flip else gray
        rects, counts = load_cascade(name).detectMultiScale2(
            g, scaleFactor=1.08, minNeighbors=neighbors, minSize=(min_side, min_side))
        found = [(tuple(int(v) for v in r), int(c)) for r, c in zip(rects, counts)]
        if not found:
            return []
        # 確からしさ（重なった検出の数）が高い順。弱すぎる誤検出は捨てる
        best = max(c for _, c in found)
        found = sorted((f for f in found if f[1] >= best * 0.25), key=lambda f: -f[1])
        if flip:
            found = [((W - x - w, y, w, h), c) for (x, y, w, h), c in found]
        return [r for r, _ in found]

    faces: list = []
    for neighbors in (6, 3):  # まず厳しめ、見つからなければゆるめて再検索
        for name in CASCADES:
            faces = run(name, neighbors)
            if not faces and name == "haarcascade_profileface.xml":
                faces = run(name, neighbors, flip=True)  # 反対向きの横顔
            if faces:
                break
        if faces:
            break
    return [tuple(int(round(v / k)) for v in f) for f in faces]


def face_from_point(x: float, y: float, img_shape) -> tuple[int, int, int, int]:
    """手動で指定した点を中心にした顔の矩形（大きさは画像の短辺の 1/4）。"""
    s = min(img_shape[:2]) / 4
    return int(x - s / 2), int(y - s / 2), int(s), int(s)


# ---------- 画像処理 ----------
def blur_image(img: np.ndarray, strength: int) -> np.ndarray:
    # 画像サイズに比例したぼかし量（strength=25 で短辺の約 2.5%）
    k = max(3, int(min(img.shape[:2]) * strength / 1000) * 2 + 1)
    return cv2.GaussianBlur(img, (k, k), 0)


def circle_mask(shape, circles, feather: int) -> np.ndarray:
    """円の内側が 1.0、外側が 0.0 のマスク（境目は feather px でなめらかに）。"""
    h, w = shape[:2]
    mask = np.zeros((h, w), np.float32)
    for (cx, cy, r) in circles:
        cv2.circle(mask, (int(cx), int(cy)), int(r), 1.0, -1, lineType=cv2.LINE_AA)
    if feather > 0:
        k = feather * 2 + 1
        mask = cv2.GaussianBlur(mask, (k, k), 0)
    return mask


def face_to_circle(face, scale: float) -> tuple[float, float, float]:
    x, y, w, h = face
    cx, cy = x + w / 2, y + h / 2
    r = max(w, h) / 2 * scale
    return cx, cy, r


def process(img: np.ndarray, faces, args) -> np.ndarray:
    targets = faces if (args.all_faces and args.mode == "full") else faces[:1]
    circles = [face_to_circle(f, args.scale) for f in targets]

    # 画像の端を越える円にも対応できるよう、端の色でパディングしておく
    pad = int(max(r for _, _, r in circles) * args.margin) + 2
    big = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    circles_p = [(cx + pad, cy + pad, r) for cx, cy, r in circles]

    feather = max(0, int(circles[0][2] * args.feather / 100))
    mask = circle_mask(big.shape, circles_p, feather)[..., None]

    if args.mode == "transparent":
        alpha = (mask[..., 0] * 255).astype(np.uint8)
        out = np.dstack([big, alpha])
    else:
        blurred = blur_image(big, args.blur)
        if args.dim > 0:
            blurred = (blurred.astype(np.float32) * (1 - args.dim / 100)).astype(np.uint8)
        out = (big * mask + blurred * (1 - mask)).astype(np.uint8)
        if args.border > 0:
            for cx, cy, r in circles_p:
                cv2.circle(out, (int(cx), int(cy)), int(r), (255, 255, 255),
                           args.border, lineType=cv2.LINE_AA)

    if args.mode == "full":
        return out[pad:pad + img.shape[0], pad:pad + img.shape[1]]

    # crop / transparent: 顔の円を中心にした正方形で切り出す
    # 顔を中央に保ったまま、なるべく元画像の内側に収まるよう余白を縮める
    # （円そのものが画像からはみ出す場合だけ、端の色を引き延ばして補う）
    cx, cy, r = circles_p[0]
    half = r * (args.margin if args.mode == "crop" else 1.0)
    room = min(cx - pad, cy - pad, pad + img.shape[1] - cx, pad + img.shape[0] - cy)
    half = max(r, min(half, room))
    x0, y0 = int(round(cx - half)), int(round(cy - half))
    side = int(round(half * 2))
    crop = out[y0:y0 + side, x0:x0 + side]
    if args.size:
        crop = cv2.resize(crop, (args.size, args.size), interpolation=cv2.INTER_AREA
                          if args.size < side else cv2.INTER_CUBIC)
    return crop


def output_path(src: Path, args, multi: bool) -> Path:
    ext = ".png" if args.mode == "transparent" else (args.format or ".png")
    if args.output and not multi:
        p = Path(args.output)
        if args.mode == "transparent" and p.suffix.lower() != ".png":
            p = p.with_suffix(".png")  # 透明は PNG のみ
        return p
    out_dir = Path(args.output) if args.output else src.parent
    return out_dir / f"{src.stem}_circle{ext}"


def collect_inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        else:
            files.append(p)
    return files


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="顔を自動検出して、顔中心に丸く切り抜き、円の外側をぼかします。")
    ap.add_argument("inputs", nargs="+", help="入力画像（複数・フォルダ指定も可）")
    ap.add_argument("-o", "--output",
                    help="出力ファイル名（入力が複数のときは出力フォルダ）。省略時は <元の名前>_circle.png")
    ap.add_argument("--mode", choices=["crop", "full", "transparent"], default="crop",
                    help="crop: 顔の周りを正方形に切り出し、円の外をぼかす（既定） / "
                         "full: 元のサイズのまま円の外をぼかす / "
                         "transparent: 円の外を透明にした PNG（アイコン向け）")
    ap.add_argument("--scale", type=float, default=1.8,
                    help="円の大きさ（検出した顔の幅に対する倍率、既定 1.8。髪まで入れるなら 2.0〜2.4）")
    ap.add_argument("--blur", type=int, default=25, help="ぼかしの強さ（既定 25。大きいほど強い）")
    ap.add_argument("--feather", type=float, default=6,
                    help="円の境目のなめらかさ（半径に対する %%、既定 6。0 でくっきり）")
    ap.add_argument("--margin", type=float, default=1.6,
                    help="crop モードで円の外のぼかし部分をどれだけ残すか（半径の倍率、既定 1.6）")
    ap.add_argument("--dim", type=float, default=0, help="背景を暗くする量 %%（既定 0）")
    ap.add_argument("--border", type=int, default=0, help="円のふちに白い線を描く太さ px（既定 0 = なし）")
    ap.add_argument("--size", type=int, help="出力を指定ピクセルの正方形にリサイズ（crop/transparent）")
    ap.add_argument("--all-faces", action="store_true", help="full モードで写っている全員の顔を丸く残す")
    ap.add_argument("--format", choices=[".png", ".jpg"], help="出力形式（既定 .png）")
    return ap


def default_options(**overrides) -> argparse.Namespace:
    """GUI などから使う既定値つきのオプション。"""
    args = build_parser().parse_args(["_"])
    args.inputs = []
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    files = collect_inputs(args.inputs)
    multi = len(files) > 1
    failed = 0
    for src in files:
        try:
            img = read_image(src)
            faces = detect_faces(img)
            if not faces:
                print(f"[スキップ] 顔が見つかりませんでした: {src}", file=sys.stderr)
                failed += 1
                continue
            out = process(img, faces, args)
            dst = output_path(src, args, multi)
            write_image(dst, out)
            print(f"[OK] {src} -> {dst}（検出した顔: {len(faces)} 件）")
        except Exception as e:  # noqa: BLE001
            print(f"[エラー] {src}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed and failed == len(files) else 0


if __name__ == "__main__":
    sys.exit(main())
