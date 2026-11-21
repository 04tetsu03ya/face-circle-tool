#!/usr/bin/env python3
"""顔の丸切り抜き + 背景ぼかしツール（画面版）。"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import cv2
import numpy as np

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
import face_circle_blur as core  # noqa: E402
import updater  # noqa: E402

try:  # 画面へのドラッグ&ドロップ（同梱の tkinterdnd2 を使う）
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # noqa: BLE001
    TkinterDnD = None

APP_NAME = "顔の丸切り抜き＋背景ぼかし"
MODES = [("切り抜き＋背景ぼかし", "crop"), ("元のサイズのまま背景ぼかし", "full"),
         ("円の外を透明に（アイコン用 PNG）", "transparent")]
SIZES = ["元のまま", "256 px", "512 px", "1024 px"]
LOG = APP_DIR / "error_log.txt"


def log_error(where: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"--- {where}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def enable_hidpi() -> None:
    """Windows の高解像度ディスプレイで文字がぼやけないようにする。"""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


class App:
    def __init__(self, root: tk.Tk, initial: list[str]):
        self.root = root
        self.files: list[Path] = []
        self.cache: dict[Path, tuple] = {}     # path -> (img, faces)
        self.manual: dict[Path, tuple] = {}    # path -> 手動で指定した顔
        self.picking = False                   # 顔の位置をクリックで指定中
        self.view = None                       # プレビューの (縮小率, 横オフセット, 縦オフセット)
        self.drag = None                       # ドラッグ開始時の (x, y, 顔の矩形, 向き)
        self.out_dir: Path | None = None
        self._job = None
        self._photo = None
        self.view_scale = 1.0                  # プレビュー用に縮小した倍率

        self.ui = root.winfo_fpixels("1i") / 96  # 画面の拡大率（100% = 1.0）
        self.preview_px = int(380 * self.ui)
        pad = int(10 * self.ui)

        root.title(f"{APP_NAME}  v{updater.current_version()}")
        main = ttk.Frame(root, padding=pad)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y", padx=(0, pad))
        wrap = int(300 * self.ui)

        ttk.Button(left, text="画像を選ぶ…", command=self.pick).pack(fill="x", ipady=int(3 * self.ui))
        hint = "（画像をこの画面にドラッグ&ドロップしてもOK）" if TkinterDnD else ""
        self.files_label = ttk.Label(left, text="画像が選ばれていません" + hint, wraplength=wrap)
        self.files_label.pack(anchor="w", pady=(4, pad))

        box = ttk.LabelFrame(left, text="仕上がり", padding=pad // 2 + 2)
        box.pack(fill="x")
        self.mode = tk.StringVar(value="crop")
        for label, val in MODES:
            ttk.Radiobutton(box, text=label, value=val, variable=self.mode,
                            command=self.schedule).pack(anchor="w")

        box2 = ttk.LabelFrame(left, text="調整", padding=pad // 2 + 2)
        box2.pack(fill="x", pady=pad // 2 + 2)
        self.scale = self._slider(box2, "円の大きさ", 1.0, 3.5, 1.8, 0.1)
        self.blur = self._slider(box2, "ぼかしの強さ", 5, 80, 25, 1)
        self.dim = self._slider(box2, "背景を暗く", 0, 60, 0, 1)
        self.border = tk.BooleanVar(value=False)
        ttk.Checkbutton(box2, text="円のふちに白い線", variable=self.border,
                        command=self.schedule).pack(anchor="w", pady=(4, 0))
        self.all_faces = tk.BooleanVar(value=False)
        ttk.Checkbutton(box2, text="全員の顔を残す（元のサイズのときのみ）",
                        variable=self.all_faces, command=self.schedule).pack(anchor="w")

        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Label(row, text="出力サイズ").pack(side="left")
        self.size = tk.StringVar(value=SIZES[0])
        ttk.OptionMenu(row, self.size, SIZES[0], *SIZES).pack(side="left", padx=4)

        self.out_label = ttk.Label(left, text="保存先: 元の画像と同じフォルダ", wraplength=wrap)
        self.out_label.pack(anchor="w", pady=(pad, 2))
        ttk.Button(left, text="保存先を変える…", command=self.pick_out).pack(fill="x")
        self.save_btn = ttk.Button(left, text="保存する", command=self.save, state="disabled")
        self.save_btn.pack(fill="x", pady=(pad, 0), ipady=int(6 * self.ui))

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="プレビュー（1枚目）").pack(side="left")
        self.pick_btn = ttk.Button(head, text="顔の位置を手で指定", command=self.toggle_pick,
                                   state="disabled")
        self.pick_btn.pack(side="right")
        self.canvas = tk.Canvas(right, width=self.preview_px, height=self.preview_px,
                                background="#d0d0d0", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(4, 0))
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)                    # Windows / Mac
        self.canvas.bind("<Button-4>", lambda e: self.on_wheel(e, +1))     # Linux
        self.canvas.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        self.canvas.bind("<Configure>", lambda e: self.schedule())
        self.status = ttk.Label(right, text="", wraplength=self.preview_px)
        self.status.pack(anchor="w", pady=(4, 0))
        self.show_text("ここに画像をドラッグ&ドロップ\nまたは「画像を選ぶ…」" if TkinterDnD
                       else "「画像を選ぶ…」から画像を開いてください")

        if TkinterDnD:
            for w in (root, self.canvas):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self.on_drop)

        if initial:
            self.set_files(core.collect_inputs(initial))
        threading.Thread(target=self.check_update, daemon=True).start()

    def _slider(self, parent, label, lo, hi, init, step):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        var = tk.DoubleVar(value=init)
        text = ttk.Label(frame, width=13, text=f"{label} {init}")
        text.pack(side="left")

        def changed(*_):
            v = round(var.get() / step) * step
            text.config(text=f"{label} {v:.1f}" if step < 1 else f"{label} {int(v)}")
            self.schedule()
        ttk.Scale(frame, from_=lo, to=hi, variable=var, command=changed).pack(
            side="left", fill="x", expand=True)
        var.trace_add("write", lambda *_: text.config(
            text=f"{label} {var.get():.1f}" if step < 1 else f"{label} {int(var.get())}"))
        return var

    # ---------- ファイル ----------
    def pick(self):
        paths = filedialog.askopenfilenames(
            title="画像を選ぶ（複数可）",
            filetypes=[("画像", "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff"), ("すべて", "*.*")])
        if paths:
            self.set_files([Path(p) for p in paths])

    def on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        files = [f for f in core.collect_inputs(list(paths)) if f.suffix.lower() in core.IMAGE_EXTS]
        if files:
            self.set_files(files)
        else:
            self.status.config(text="画像ファイル（jpg / png など）をドロップしてください")
        return event.action

    def pick_out(self):
        d = filedialog.askdirectory(title="保存先フォルダ")
        if d:
            self.out_dir = Path(d)
            self.out_label.config(text=f"保存先: {d}")

    def set_files(self, files: list[Path]):
        self.files = files
        self.picking = False
        n = len(files)
        self.files_label.config(text=files[0].name if n == 1 else f"{files[0].name} ほか {n - 1} 枚")
        self.save_btn.config(state="normal" if n else "disabled")
        self.pick_btn.config(state="normal" if n else "disabled")
        self.schedule()

    def load(self, path: Path):
        if path not in self.cache:
            img = core.read_image(path)
            self.cache[path] = (img, core.detect_faces(img))
        img, faces = self.cache[path]
        if path in self.manual:
            faces = [self.manual[path]]
        return img, faces

    # ---------- 顔の位置の手動指定 ----------
    def toggle_pick(self):
        self.picking = not self.picking
        self.schedule()

    def to_image_xy(self, event):
        k, ox, oy = self.view
        return (event.x - ox) / k, (event.y - oy) / k

    def on_press(self, event):
        if not (self.files and self.view):
            return
        path = self.files[0]
        img, faces = self.load(path)
        if self.picking:  # クリックした所を顔の中心にする
            x, y = self.to_image_xy(event)
            if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                self.manual[path] = core.face_from_point(x, y, img.shape)
                self.picking = False
                self.schedule()
            return
        if faces:
            # 切り抜き表示では写真を動かす（円は真ん中に固定）、元サイズ表示では円を動かす
            direction = 1 if self.mode.get() == "full" else -1
            self.drag = (event.x, event.y, faces[0], direction)
            self.canvas.config(cursor="fleur")

    def on_drag(self, event):
        if not self.drag or not self.view:
            return
        x0, y0, (fx, fy, fw, fh), direction = self.drag
        k = self.view[0] * self.view_scale
        dx, dy = (event.x - x0) / k * direction, (event.y - y0) / k * direction
        img, _ = self.cache[self.files[0]]
        cx = min(max(fx + fw / 2 + dx, 0), img.shape[1])
        cy = min(max(fy + fh / 2 + dy, 0), img.shape[0])
        self.manual[self.files[0]] = (int(cx - fw / 2), int(cy - fh / 2), fw, fh)
        self.schedule(delay=15)

    def on_release(self, event):
        if self.drag:
            self.drag = None
            self.canvas.config(cursor="")
            self.schedule()

    def on_wheel(self, event, step=None):
        if not self.files or self.picking:
            return
        up = step if step is not None else (1 if event.delta > 0 else -1)
        self.scale.set(round(min(3.5, max(1.0, self.scale.get() + 0.1 * up)), 1))
        self.schedule()

    # ---------- 処理 ----------
    def options(self):
        size = int(self.size.get().split()[0]) if self.size.get()[0].isdigit() else None
        return core.default_options(
            mode=self.mode.get(), scale=round(self.scale.get(), 1), blur=int(self.blur.get()),
            dim=int(self.dim.get()), border=max(2, int(min(6 * self.ui, 12))) if self.border.get() else 0,
            all_faces=self.all_faces.get(), size=size)

    def schedule(self, *_, delay=120):
        if self._job:
            self.root.after_cancel(self._job)
        self._job = self.root.after(delay, self.preview)

    def show_text(self, text):
        self.canvas.delete("all")
        self.view = None
        w = max(self.canvas.winfo_width(), self.preview_px)
        h = max(self.canvas.winfo_height(), self.preview_px)
        self.canvas.create_text(w // 2, h // 2, text=text, justify="center", fill="#333")

    def show_image(self, bgr):
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        h, w = bgr.shape[:2]
        k = min(cw / w, ch / h)
        small = cv2.resize(bgr, (max(1, int(w * k)), max(1, int(h * k))),
                           interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
        if small.shape[2] == 4:  # 透明部分は市松模様で見せる
            yy, xx = np.indices(small.shape[:2])
            check = (((yy // 12 + xx // 12) % 2) * 40 + 200).astype(np.uint8)
            a = small[..., 3:4] / 255.0
            small = (small[..., :3] * a + cv2.merge([check] * 3) * (1 - a)).astype(np.uint8)
        ok, buf = cv2.imencode(".png", small)
        self._photo = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
        ox, oy = (cw - small.shape[1]) // 2, (ch - small.shape[0]) // 2
        self.canvas.delete("all")
        self.canvas.create_image(ox, oy, anchor="nw", image=self._photo)
        self.view = (k, ox, oy)

    def preview(self):
        self._job = None
        if not self.files:
            return
        path = self.files[0]
        try:
            img, faces = self.load(path)
        except Exception as e:  # noqa: BLE001
            log_error(f"load {path}")
            self.show_text(f"画像を読み込めませんでした\n{e}")
            self.status.config(text="")
            return
        if self.picking or not faces:
            self.view_scale = 1.0
            self.show_image(img)
            self.pick_btn.config(text="指定をやめる" if self.picking else "顔の位置を手で指定")
            if not faces:
                self.picking = True
                self.pick_btn.config(text="指定をやめる")
                self.status.config(text="顔が自動で見つかりませんでした。写真の顔をクリックしてください。")
            else:
                self.status.config(text="写真の顔の中心をクリックしてください。")
            return
        try:
            opts = self.options()
            opts.size = None
            # プレビューは縮小した画像で計算して軽くする（ドラッグを滑らかにするため）
            r = max(faces[0][2], faces[0][3]) / 2 * opts.scale
            out_side = max(img.shape[:2]) if opts.mode == "full" else 2 * r * opts.margin
            s = min(1.0, self.preview_px * 1.2 / max(out_side, 1))
            if s < 1:
                img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                faces = [tuple(int(v * s) for v in f) for f in faces]
                opts.border = max(1, round(opts.border * s)) if opts.border else 0
            self.view_scale = s
            self.show_image(core.process(img, faces, opts))
        except Exception as e:  # noqa: BLE001
            log_error(f"process {path}")
            self.show_text(f"処理中にエラーが起きました\n{e}")
            return
        self.pick_btn.config(text="顔の位置を手で指定")
        how = "手で調整した位置" if path in self.manual else f"自動で見つけた顔 {len(faces)} 件"
        self.status.config(text=f"{how}。プレビューをドラッグで位置、ホイールで円の大きさを調整できます。")

    def save(self):
        opts = self.options()
        if self.out_dir:
            opts.output = str(self.out_dir)
        saved, skipped = [], []
        self.root.config(cursor="watch")
        self.root.update()
        try:
            for src in self.files:
                try:
                    img, faces = self.load(src)
                    if not faces:
                        skipped.append(f"{src.name}（顔が見つかりませんでした）")
                        continue
                    dst = core.output_path(src, opts, multi=True)
                    core.write_image(dst, core.process(img, faces, opts))
                    saved.append(dst)
                except Exception as e:  # noqa: BLE001
                    log_error(f"save {src}")
                    skipped.append(f"{src.name}（{e}）")
        finally:
            self.root.config(cursor="")
        msg = f"{len(saved)} 枚保存しました。"
        if skipped:
            msg += "\n\n保存できなかった画像:\n" + "\n".join(skipped)
            if any("顔が見つかりません" in s for s in skipped):
                msg += "\n\n1枚ずつ開いて「顔の位置を手で指定」すると保存できます。"
        messagebox.showinfo("完了", msg)
        if saved:
            open_folder(saved[0].parent)

    # ---------- 自動更新 ----------
    def check_update(self):
        try:
            info = updater.check()
        except Exception:  # noqa: BLE001  ネットにつながらないときなどは黙って続ける
            return
        if info:
            self.root.after(0, lambda: self.offer_update(info))

    def offer_update(self, info):
        notes = f"\n\n変更点:\n{info['notes'][:600]}" if info.get("notes") else ""
        if not messagebox.askyesno(
                "更新があります",
                f"新しいバージョン {info['version']} があります（今は {updater.current_version()}）。"
                f"\n今すぐ更新しますか？{notes}"):
            return
        try:
            self.root.config(cursor="watch")
            self.root.update()
            updater.apply(info)
        except Exception as e:  # noqa: BLE001
            log_error("update")
            self.root.config(cursor="")
            messagebox.showerror("更新できませんでした", f"{e}\n\n今のバージョンのまま使えます。")
            return
        messagebox.showinfo("更新しました", f"バージョン {info['version']} に更新しました。ツールを開き直します。")
        args = [sys.executable, str(APP_DIR / "gui.py"), *[str(p) for p in self.files]]
        subprocess.Popen(args, close_fds=True)
        self.root.destroy()


def open_folder(path: Path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        pass


def setup_fonts(root: tk.Tk) -> None:
    if sys.platform != "win32":
        return
    families = set(tkfont.families(root))
    family = next((f for f in ("Yu Gothic UI", "Meiryo UI", "Meiryo", "MS UI Gothic")
                   if f in families), None)
    if not family:
        return
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=family, size=10)
        except tk.TclError:
            pass


def main():
    enable_hidpi()
    root = None
    if TkinterDnD:
        try:
            root = TkinterDnD.Tk()
        except Exception:  # noqa: BLE001
            log_error("tkdnd")
            root = None
    if root is None:
        globals()["TkinterDnD"] = None
        root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72)
    except tk.TclError:
        pass
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    setup_fonts(root)
    try:
        App(root, sys.argv[1:])
    except Exception:  # noqa: BLE001
        log_error("startup")
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
