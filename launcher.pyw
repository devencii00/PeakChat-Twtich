#!/usr/bin/env python3
"""
PeakChat Launcher — splash screen edition
Opens a small landscape window, checks/install dependencies, and launches
twitch_peakchat.py once ready. Pure tkinter — no external deps needed.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import random
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path


# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent
VENV_DIR = APP_DIR / ".venv"
SCRIPT_NAME = "twitch_peakchat.py"

W, H = 720, 420          # landscape window size
R = 18                   # rounded corner radius
KEY = "#010203"          # transparency key

# Palette
BG1 = (20, 17, 42)
BG2 = (8, 10, 22)
ACCENT_A = (125, 92, 255)
ACCENT_B = (0, 205, 255)
TEXT = (232, 234, 250)
SUBTEXT = (140, 146, 180)
OK_COL = (90, 220, 140)
ERR_COL = (255, 90, 90)

PACKAGES = [
    ("customtkinter",  ["customtkinter"],                       "customtkinter"),
    ("websockets",     ["websockets"],                          "websockets"),
    ("requests",       ["requests"],                            "requests"),
    ("Pillow",         ["Pillow"],                              "Pillow"),
    ("tkinter-icons[material]", ["tkinter-icons-mat"],          "tkinter-icons[material]"),
    ("pygame-ce",      ["pygame-ce", "pygame"],                 "pygame-ce"),
    ("chat-downloader",["chat-downloader", "chat_downloader"],  "chat-downloader"),
]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROBE = r'''
import json, sys
from importlib import metadata
spec = json.loads(sys.argv[1])
out = {}
for disp, names in spec:
    ver = None
    for n in names:
        try:
            ver = metadata.version(n)
            break
        except Exception:
            pass
    out[disp] = ver
print("PROBE:" + json.dumps(out))
'''


# ────────────────────────────────────────────────────────────────────────────
# Small helpers
# ────────────────────────────────────────────────────────────────────────────
def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def hx(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


_COS = math.cos(math.radians(65))
_SIN = math.sin(math.radians(65))
_DEN = W * _COS + H * _SIN


def bg_at(x, y):
    t = (x * _COS + y * _SIN) / _DEN
    return mix(BG1, BG2, max(0.0, min(1.0, t)))


def arc_pts(cx, cy, r, a0, a1, n=12):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
            for i in range(n + 1)]


def flat(pts):
    return [v for p in pts for v in p]


def build_bg(root):
    """Cheap diagonal gradient: half res, then zoom 2x."""
    w, h = W // 2, H // 2
    lut = [hx(mix(BG1, BG2, i / 255)) for i in range(256)]
    den = w * _COS + h * _SIN
    rows = []
    for y in range(h):
        base = y * _SIN
        rows.append("{" + " ".join(
            lut[min(255, int((x * _COS + base) / den * 255))] for x in range(w)
        ) + "}")
    img = tk.PhotoImage(master=root, width=w, height=h)
    img.put(" ".join(rows))
    return img.zoom(2, 2)


def venv_pythonw():
    if os.name == "nt":
        p = VENV_DIR / "Scripts" / "pythonw.exe"
        return p if p.exists() else VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_python():
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def base_python():
    exe = Path(sys.executable)
    if os.name == "nt":
        c = exe.with_name("python.exe")
        if c.exists():
            return str(c)
    return str(exe)


# ────────────────────────────────────────────────────────────────────────────
# Particle field
# ────────────────────────────────────────────────────────────────────────────
class Particles:
    def __init__(self, app, count=26):
        self.app = app
        self.cv = app.cv
        self.rng = random.Random()
        self.parts = []
        self.t = 0.0
        self.count = count

    def _items(self, p):
        if p["cube"]:
            return (p["outer"], p["inner"])
        return (p["glow"], p["core"])

    def spawn(self, random_y):
        cv = self.cv
        s = self.rng.randint(14, 34)
        cube = self.rng.random() < 0.6
        col = mix(ACCENT_A, ACCENT_B, self.rng.random())
        x = self.rng.uniform(0, W)
        y = self.rng.uniform(0, H) if random_y else H + s
        p = {
            "x": x, "y": y,
            "vx": self.rng.uniform(-6, 6),
            "vy": -self.rng.uniform(8, 22),
            "vr": self.rng.uniform(-18, 18),
            "rot": self.rng.uniform(0, 90),
            "size": s,
            "phase": self.rng.uniform(0, math.tau),
            "cube": cube,
            "col": col,
        }
        bg = bg_at(x, max(0, min(H, y)))
        if cube:
            p["outer"] = cv.create_polygon(
                0, 0, 0, 0, 0, 0, 0, 0, fill="",
                outline=hx(mix(bg, col, 0.75)), width=1.5, tags=("particle",),
            )
            p["inner"] = cv.create_polygon(
                0, 0, 0, 0, 0, 0, 0, 0,
                fill=hx(mix(bg, col, 0.30)),
                outline=hx(mix(bg, col, 0.55)), width=1, tags=("particle",),
            )
        else:
            p["d"] = max(3, s // 4)
            p["glow"] = cv.create_oval(
                0, 0, 0, 0,
                outline=hx(mix(bg, col, 0.30)), width=3, tags=("particle",),
            )
            p["core"] = cv.create_oval(
                0, 0, 0, 0,
                fill=hx(mix(bg, col, 0.85)), outline="", tags=("particle",),
            )
        for it in self._items(p):
            try:
                cv.tag_raise(it, self.app.bg_id)   # just above bg, below UI
            except tk.TclError:
                pass
        self.place(p)
        return p

    def place(self, p):
        cv = self.cv
        x, y, s = p["x"], p["y"], p["size"]
        if p["cube"]:
            a = math.radians(p["rot"])
            ca, sa = math.cos(a), math.sin(a)
            h = s / 2
            pts = []
            for lx, ly in ((-h, -h), (h, -h), (h, h), (-h, h)):
                pts += [x + lx * ca - ly * sa, y + lx * sa + ly * ca]
            cv.coords(p["outer"], *pts)
            off = 0.075 * s
            ix, iy = x + off * ca - off * sa, y + off * sa + off * ca
            h2 = s * 0.275
            pts2 = []
            for lx, ly in ((-h2, -h2), (h2, -h2), (h2, h2), (-h2, h2)):
                pts2 += [ix + lx * ca - ly * sa, iy + lx * sa + ly * ca]
            cv.coords(p["inner"], *pts2)
        else:
            r = p["d"] / 2
            g = r + 2
            cv.coords(p["core"], x - r, y - r, x + r, y + r)
            cv.coords(p["glow"], x - g, y - g, x + g, y + g)

    def populate(self):
        self.parts = [self.spawn(True) for _ in range(self.count)]

    def step(self, dt):
        for i, p in enumerate(self.parts):
            p["y"] += p["vy"] * dt
            p["x"] += (p["vx"] + math.sin(self.t * 0.6 + p["phase"]) * 4) * dt
            p["rot"] += p["vr"] * dt
            if p["y"] < -p["size"] - 4:
                for it in self._items(p):
                    self.cv.delete(it)
                self.parts[i] = self.spawn(False)
                continue
            if p["x"] < -p["size"]:
                p["x"] = W + p["size"]
            elif p["x"] > W + p["size"]:
                p["x"] = -p["size"]
            self.place(p)


# ────────────────────────────────────────────────────────────────────────────
# Launcher window
# ────────────────────────────────────────────────────────────────────────────
class Launcher:
    def __init__(self):
        # window
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *a: None
        self.root.title("PeakChat")
        self.root.overrideredirect(True)
        self.root.configure(bg=KEY)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
        try:
            self.root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        # fonts
        fams = set(tkfont.families(self.root))
        self.ff = next(
            (f for f in ("Segoe UI", "Inter", "SF Pro Text",
                         "Helvetica Neue", "DejaVu Sans", "Arial")
             if f in fams),
            "TkDefaultFont",
        )

        # state
        self.q = queue.Queue()
        self.state = "checking"
        self.steps = [
            {"name": "Environment",  "status": "pending", "detail": "Waiting"},
            {"name": "Dependencies", "status": "pending", "detail": "Waiting"},
            {"name": "Application",  "status": "pending", "detail": "Waiting"},
        ]
        self.progress = 0.0
        self.target_progress = 0.0
        self.proc = None
        self.t = 0.0
        self.last = time.perf_counter()
        self._drag = None
        self.step_ui = []
        self.error_shown = False

        # colours
        self.c_bg_mid = bg_at(W * 0.4, H * 0.5)
        self.c_stroke = mix(self.c_bg_mid, (70, 66, 120), 0.5)

        # canvas + bg image
        self.cv = cv = tk.Canvas(self.root, width=W, height=H,
                                 bg=KEY, highlightthickness=0, bd=0)
        cv.pack()
        self.bg_img = build_bg(self.root)
        self.bg_id = cv.create_image(0, 0, image=self.bg_img,
                                     anchor="nw", tags=("bg",))

        # UI first, then particles (so particles end up below UI)
        self._build_ui()
        self.particles = Particles(self, count=26)
        self.particles.populate()

        # rounded-corner mask + animated gradient border on top
        self._build_mask()

        # bindings
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._motion)
        self.root.bind("<Escape>", lambda e: self.close())

        # launch sequence
        self.root.after(50, self._fade_in)
        self.last = time.perf_counter()
        self.root.after(16, self.tick)

    # ---------------------------------------------------------------- drawing
    def rrect(self, x1, y1, x2, y2, r, fill="", outline="", width=1, tags=()):
        pts = [
            x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
            x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
            x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
            x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1,
        ]
        return self.cv.create_polygon(pts, smooth=True, fill=fill,
                                      outline=outline, width=width, tags=tags)

    def _build_ui(self):
        cv = self.cv

        # minimize + close
        self._make_btn(W - 88, 16, 30, 30, "min", self.minimize)
        self._make_btn(W - 52, 16, 30, 30, "x",   self.close)

        # main title
        self.title_text = cv.create_text(
            W / 2, 148,
            text="Starting PeakChat...",
            fill=hx(TEXT),
            font=(self.ff, -26, "bold"),
            anchor="center",
        )
        # subtitle
        self.subtitle = cv.create_text(
            W / 2, 182,
            text="Preparing your workspace",
            fill=hx(SUBTEXT),
            font=(self.ff, -12),
            anchor="center",
        )

        # progress bar (must come BEFORE the percent label gets referenced)
        self._build_pbar()

        # percent label — created AFTER the bar so set_progress can find it
        self.pct_text = cv.create_text(
            W / 2, 240,
            text="0%",
            fill=hx(SUBTEXT),
            font=(self.ff, -11, "bold"),
            anchor="center",
        )

        # paint the bar at 0% now that pct_text exists
        self.set_progress(0)

        # step rows
        for i in range(len(self.steps)):
            self.step_ui.append(self._build_step(i))

    def _make_btn(self, x, y, w, h, kind, cmd):
        cv = self.cv
        tag = f"btn_{kind}_{id(cmd)}"
        if kind == "x":
            bg = mix(self.c_bg_mid, (180, 45, 45), 0.5)
            bg_h = mix(self.c_bg_mid, (220, 60, 60), 0.6)
        else:
            bg = mix(self.c_bg_mid, (24, 24, 44), 0.9)
            bg_h = mix(self.c_bg_mid, (24, 24, 44), 1.0)

        rect = self.rrect(x, y, x + w, y + h, 8,
                          fill=hx(bg), outline=hx(self.c_stroke),
                          tags=(tag, "btn"))
        if kind == "min":
            cv.create_line(x + w / 2 - 5, y + h / 2, x + w / 2 + 5, y + h / 2,
                           fill=hx(TEXT), width=2, capstyle="round",
                           tags=(tag, "btn"))
        else:
            cx, cy = x + w / 2, y + h / 2
            cv.create_line(cx - 4, cy - 4, cx + 4, cy + 4,
                           fill=hx(TEXT), width=2, capstyle="round",
                           tags=(tag, "btn"))
            cv.create_line(cx - 4, cy + 4, cx + 4, cy - 4,
                           fill=hx(TEXT), width=2, capstyle="round",
                           tags=(tag, "btn"))

        def enter(_e):
            cv.itemconfigure(rect, fill=hx(bg_h))
            cv.configure(cursor="hand2")

        def leave(_e):
            cv.itemconfigure(rect, fill=hx(bg))
            cv.configure(cursor="")

        cv.tag_bind(tag, "<Enter>", enter)
        cv.tag_bind(tag, "<Leave>", leave)
        cv.tag_bind(tag, "<Button-1>", lambda e: cmd())

    def _build_pbar(self):
        cv = self.cv
        x0, y0 = 80, 214
        w, h = W - 160, 8
        r = h / 2
        self.pbar = {"x0": x0, "y0": y0, "w": w, "h": h, "r": r}

        track_bg = mix(self.c_bg_mid, (60, 60, 100), 0.6)
        self.rrect(x0, y0, x0 + w, y0 + h, r,
                   fill=hx(track_bg), outline="")

        n = 100
        span = w - h
        self.strips = []
        for i in range(n):
            sx = x0 + r + span * i / n
            col = hx(mix(ACCENT_A, ACCENT_B, i / (n - 1)))
            seg = cv.create_line(
                sx, y0 + h / 2, sx + span / n + 1, y0 + h / 2,
                fill=col, width=h, capstyle="butt",
            )
            self.strips.append(seg)

            self.cap_l = cv.create_oval(x0, y0, x0 + h, y0 + h,
                                    fill=hx(ACCENT_A), outline="")
        self.cap_r = cv.create_oval(0, 0, 0, 0,
                                    fill=hx(ACCENT_A), outline="")
        # NOTE: do NOT call set_progress here — pct_text doesn't exist yet.

    def set_progress(self, p):
        p = max(0.0, min(1.0, p))
        x0, y0, w, h, r = (self.pbar["x0"], self.pbar["y0"],
                           self.pbar["w"], self.pbar["h"], self.pbar["r"])
        span = w - h
        head = x0 + r + span * p

        k = int(p * len(self.strips))
        for i, s in enumerate(self.strips):
            self.cv.itemconfigure(s, state="normal" if i < k else "hidden")

        if p > 0.005:
            self.cv.itemconfigure(self.cap_l, state="normal")
            self.cv.coords(self.cap_r, head - r, y0, head + r, y0 + h)
            self.cv.itemconfigure(
                self.cap_r,
                state="normal",
                fill=hx(mix(ACCENT_A, ACCENT_B, p)),
            )
        else:
            self.cv.itemconfigure(self.cap_l, state="hidden")
            self.cv.itemconfigure(self.cap_r, state="hidden")

        self.cv.itemconfigure(self.pct_text, text=f"{int(p * 100)}%")

    def _build_step(self, i):
        y = 270 + i * 30
        cx = 100
        cv = self.cv
        icon = cv.create_oval(cx - 5, y - 5, cx + 5, y + 5,
                              fill=hx(SUBTEXT), outline="")
        name = cv.create_text(
            cx + 18, y,
            text=self.steps[i]["name"],
            fill=hx(TEXT),
            font=(self.ff, -12, "bold"),
            anchor="w",
        )
        detail = cv.create_text(
            W - 100, y,
            text=self.steps[i]["detail"],
            fill=hx(SUBTEXT),
            font=(self.ff, -11),
            anchor="e",
        )
        return {"icon": icon, "name": name, "detail": detail}

    def _update_step(self, i, status, detail=None):
        self.steps[i]["status"] = status
        if detail is not None:
            self.steps[i]["detail"] = detail
        ui = self.step_ui[i]
        color = {
            "pending": SUBTEXT,
            "running": ACCENT_B,
            "ok":      OK_COL,
            "error":   ERR_COL,
        }[status]
        self.cv.itemconfigure(ui["icon"], fill=hx(color))
        self.cv.itemconfigure(ui["detail"], text=self.steps[i]["detail"])

    def _build_mask(self):
        """Cut rounded corners only (no border outline)."""
        cv = self.cv

        for cx, cy, a0, a1, corner in (
            (R,     R,     180, 270, (0, 0)),
            (W - R, R,     270, 360, (W, 0)),
            (W - R, H - R,   0,  90, (W, H)),
            (R,     H - R,  90, 180, (0, H)),
        ):
            pts = flat([corner] + arc_pts(cx, cy, R, a0, a1))
            cv.create_polygon(pts, fill=KEY, outline=KEY, tags=("mask",))

        # keep the attribute so tick() doesn't crash
        self.border = []

    # ---------------------------------------------------------------- events
    def _press(self, e):
        tags = self.cv.gettags("current")
        if "btn" in tags:
            self._drag = None
            return
        self._drag = (e.x_root - self.root.winfo_x(),
                      e.y_root - self.root.winfo_y())

    def _motion(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _safe_alpha(self, a):
        try:
            self.root.attributes("-alpha", a)
        except tk.TclError:
            pass

    def _fade_in(self):
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        n = 20
        for i in range(n + 1):
            self.root.after(int(280 * i / n),
                            lambda a=i / n: self._safe_alpha(a))
        self.root.after(320, self._start_worker)

    def _fade_out_and_close(self):
        n = 20
        for i in range(n + 1):
            a = 1 - i / n
            self.root.after(int(250 * i / n),
                            lambda a=a: self._safe_alpha(a))
        self.root.after(300, self.root.destroy)

    def minimize(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            try:
                self.root.iconify()
            except tk.TclError:
                pass

    def close(self):
        if self.state == "done":
            self._fade_out_and_close()
        else:
            self._fade_out_and_close()

    # --------------------------------------------------------------- tick loop
    def tick(self):
        try:
            now = time.perf_counter()
            dt = min(0.05, now - self.last)
            self.last = now
            self.t += dt

            # smooth progress
            if abs(self.progress - self.target_progress) > 0.001:
                self.progress += (self.target_progress - self.progress) * min(1.0, dt * 6)
                self.set_progress(self.progress)

            # particles
            self.particles.t = self.t
            self.particles.step(dt)

            # (animated border removed)

            # drain queue
            self._poll()

            self.root.after(16, self.tick)
        except tk.TclError:
            return

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                k = ev[0]
                if k == "step":
                    self._update_step(ev[1], ev[2], ev[3])
                elif k == "progress":
                    self.target_progress = ev[1]
                elif k == "launching":
                    self._launch(ev[1], ev[2])
                elif k == "error":
                    self._show_error(ev[1], ev[2])
        except queue.Empty:
            pass

    # -------------------------------------------------------------- background
    def _start_worker(self):
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        q = self.q
        def emit(*a):
            q.put(a)

        try:
            # -- Step 1: virtual environment --
            emit("step", 0, "running", "Looking for .venv")
            emit("progress", 0.05)
            py = venv_python()
            if not py.exists():
                emit("step", 0, "running", "Creating .venv")
                r = subprocess.run(
                    [base_python(), "-m", "venv", str(VENV_DIR)],
                    capture_output=True, text=True, creationflags=NO_WINDOW,
                )
                if r.returncode != 0:
                    emit("error", "Could not create .venv",
                         (r.stderr or r.stdout or "")[-300:])
                    return
            emit("step", 0, "ok", ".venv ready")
            emit("progress", 0.20)

            # -- Step 2: dependencies --
            emit("step", 1, "running", "Checking packages")
            spec = json.dumps([[d, n] for d, n, _ in PACKAGES])
            versions = {}
            try:
                r = subprocess.run(
                    [str(py), "-c", PROBE, spec],
                    capture_output=True, text=True, creationflags=NO_WINDOW,
                )
                for line in r.stdout.splitlines():
                    if line.startswith("PROBE:"):
                        versions = json.loads(line[6:])
                        break
            except Exception:
                pass

            missing = [p for p in PACKAGES if not versions.get(p[0])]

            if missing:
                names = ", ".join(m[0] for m in missing)
                emit("step", 1, "running", f"Installing: {names}")
                proc = subprocess.Popen(
                    [str(py), "-m", "pip", "install", "--disable-pip-version-check",
                     *[m[2] for m in missing]],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=NO_WINDOW,
                )
                count = 0
                if proc.stdout is not None:
                    for _line in proc.stdout:
                        count += 1
                        if count % 2 == 0:
                            emit("progress",
                                 0.30 + min(0.40, count * 0.012))
                proc.wait()

                # re-check
                versions = {}
                try:
                    r = subprocess.run(
                        [str(py), "-c", PROBE, spec],
                        capture_output=True, text=True, creationflags=NO_WINDOW,
                    )
                    for line in r.stdout.splitlines():
                        if line.startswith("PROBE:"):
                            versions = json.loads(line[6:])
                            break
                except Exception:
                    pass
                still = [p[0] for p in PACKAGES if not versions.get(p[0])]
                if still:
                    emit("error", "Install failed",
                         "Missing: " + ", ".join(still))
                    return

            emit("step", 1, "ok", f"{len(PACKAGES)} packages ready")
            emit("progress", 0.78)

            # -- Step 3: application script --
            emit("step", 2, "running", "Locating script")
            sp = APP_DIR / SCRIPT_NAME
            if not sp.exists():
                emit("error", "Script not found", f"Expected: {sp}")
                return
            emit("step", 2, "ok", SCRIPT_NAME)
            emit("progress", 0.92)

            # -- Launch --
            emit("launching", str(py), str(sp))

        except Exception:
            emit("error", "Unexpected error",
                 traceback.format_exc()[-400:])

    # ------------------------------------------------------------------ launch
    def _launch(self, py, sp):
        pyw = venv_pythonw()
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = subprocess.Popen(
                [str(pyw), sp],
                cwd=str(APP_DIR), env=env,
                creationflags=NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._show_error("Could not launch app", str(e))
            return
        self.target_progress = 1.0
        self.state = "launched"
        self.root.after(2000, self._verify)

    def _verify(self):
        if not self.proc:
            return
        rc = self.proc.poll()
        if rc is None:
            # running fine
            self.state = "done"
            self.root.after(400, self._fade_out_and_close)
        else:
            tail = ""
            crash_log = APP_DIR / "app_error.log"
            if crash_log.exists():
                try:
                    tail = crash_log.read_text(errors="replace")[-400:]
                except Exception:
                    pass
            self._show_error(
                f"App exited (code {rc})",
                tail or "No error output was captured.",
            )

    # ------------------------------------------------------------------- error
    def _show_error(self, title, detail):
        if self.error_shown:
            return
        self.error_shown = True
        self.state = "error"
        cv = self.cv

        cv.itemconfigure(self.title_text, text="Launch failed", fill=hx(ERR_COL))
        cv.itemconfigure(self.subtitle, text=title, fill=hx(TEXT))

        # hide percent
        cv.itemconfigure(self.pct_text, text="", state="hidden")

                # error detail box — clear the step rows (they end around y=340)
        for ui in self.step_ui:
            self.cv.itemconfigure(ui["icon"],   state="hidden")
            self.cv.itemconfigure(ui["name"],   state="hidden")
            self.cv.itemconfigure(ui["detail"], state="hidden")
        y0 = 250
        cv.create_text(
            W / 2, y0,
            text=detail[:400],
            fill=hx(mix(ERR_COL, SUBTEXT, 0.55)),
            font=(self.ff, -10),
            anchor="n",
            width=W - 160,
            justify="center",
        )

        # Close button
        bx, by = W / 2 - 60, H - 66
        tag = "close_btn_err"
        self.rrect(bx, by, bx + 120, by + 36, 9,
                   fill=hx(ACCENT_A), outline=hx(ACCENT_B),
                   tags=(tag, "btn"))
        cv.create_text(
            W / 2, by + 18,
            text="Close",
            fill=hx(TEXT),
            font=(self.ff, -13, "bold"),
            tags=(tag, "btn"),
        )

        def enter(_e):
            cv.configure(cursor="hand2")

        def leave(_e):
            cv.configure(cursor="")

        cv.tag_bind(tag, "<Enter>", enter)
        cv.tag_bind(tag, "<Leave>", leave)
        cv.tag_bind(tag, "<Button-1>", lambda e: self._fade_out_and_close())


# ────────────────────────────────────────────────────────────────────────────
# Entry
# ────────────────────────────────────────────────────────────────────────────
def main():
    try:
        launcher = Launcher()
        launcher.root.mainloop()
    except Exception:
        try:
            err = APP_DIR / "launcher_error.log"
            err.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        if os.name == "nt":
            try:
                ctypes.windll.user32.MessageBoxW(
                    0, traceback.format_exc()[-1500:], "Launcher error", 0x10,
                )
            except Exception:
                pass


if __name__ == "__main__":
    main()