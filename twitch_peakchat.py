#!/usr/bin/env python3
"""
Twitch PeakChat — v3

New in v3:
- Import VOD is now a dropdown: Local file  |  Chat Download (URL)
- Chat Download fetches VOD chat directly via chat_downloader (no temp file)
- Custom window icon (icon.ico preferred, peakchat.png fallback)
- Smaller header (32px), tighter tab bar
- Log filenames are date-stamped: YYYY-MM-DD_channel_chat.csv
- Simplified CSV columns (Start_Time, End_Time, Duration_s, Peak, Average, Samples)
- Configurable logs folder in Settings
- Save buttons moved inside their own panels (chat panel / spike panel)
- Fullscreen on launch setting + Ctrl+Enter / F11 toggle
- Delete confirmation modal on spike cards
- Uses Material icons from tkinter-icons

Install:
    pip install customtkinter requests websockets Pillow pygame-ce chat-downloader "tkinter-icons[material]"
"""

from __future__ import annotations

import asyncio
import collections
import colorsys
import csv
import hashlib
import io
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from tkinter import filedialog

import customtkinter as ctk
import requests
import websockets
from tkinter_icons import MaterialIcon
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    import pygame
    PYGAME_OK = True
except ImportError:
    PYGAME_OK = False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(BASE_DIR, "twitch_logs")
AVATAR_DIR = os.path.join(BASE_DIR, "avatar_cache")
SAVED_STREAMERS_FILE = os.path.join(BASE_DIR, "streamers.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
SPIKE_SOUND_PATH = os.path.join(BASE_DIR, "spike_notif.mp3")
ICON_NAME = "peakchat.png"
WINDOW_ICON_NAME = "tweakchat.ico"


def find_asset(name: str) -> str | None:
    for base in (BASE_DIR, getattr(sys, "_MEIPASS", None), os.getcwd()):
        if base:
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TWITCH_IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
DECAPI_UPTIME_URL = "https://decapi.me/twitch/uptime/{channel}"
DECAPI_AVATAR_URL = "https://decapi.me/twitch/avatar/{channel}"
HTTP_UA = {"User-Agent": "DevenciiGG-PeakChat/3.0"}

BUCKET_SECONDS = 10
ROLLING_WINDOW_BUCKETS = 18
MIN_SPIKE_THRESHOLD = 10
COOLDOWN_SECONDS = 30
NO_MESSAGE_WARN_SECONDS = 60
CHAT_MAX_LINES = 1000
SPARK_SLOTS = 36
AVATAR_PX = 96

SENSITIVITY_OPTIONS = {
    "2.0x (Sensitive)": 2.0,
    "2.5x (Balanced)": 2.5,
    "3.0x (Huge Hype Only)": 3.0,
}

TWITCH_NAME_RE = re.compile(r"^[a-z0-9_]{1,25}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_SETTINGS = {
    "theme": "dark",
    "sound": True,
    "volume": 0.7,
    "particles": True,
    "default_sensitivity": "2.5x (Balanced)",
    "chat_font": 13,
    "tabs": [],
    "active": 0,
    "fullscreen": False,
    "logs_folder": "",
}
SETTINGS: dict = dict(DEFAULT_SETTINGS)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
PALETTE = {
    "bg":             ("#E8EBF7", "#090B17"),
    "surface":        ("#FFFFFF", "#14172B"),
    "surface2":       ("#F0F2FB", "#1B1F38"),
    "surface3":       ("#E2E6F6", "#262B4C"),
    "stroke":         ("#D3D8EE", "#2A2F54"),
    "text":           ("#171A33", "#E9EBFB"),
    "sub":            ("#636A90", "#8E95BA"),
    "accent":         ("#7D5CFF", "#7D5CFF"),
    "accent_hover":   ("#6B49EE", "#9378FF"),
    "cyan":           ("#0891B2", "#22D3EE"),
    "green":          ("#16A34A", "#2FA572"),
    "green_hover":    ("#15803D", "#238A5C"),
    "red":            ("#DC2626", "#D32F2F"),
    "red_hover":      ("#B91C1C", "#B71C1C"),
    "amber":          ("#D97706", "#F59E0B"),
    "gray_btn":       ("#E2E6F6", "#2A2F54"),
    "gray_btn_hover": ("#D3D8EE", "#353B66"),
}
P = PALETTE

ACCENT_A_RGB = (125, 92, 255)
ACCENT_B_RGB = (0, 205, 255)

STATUS = {
    "ok": "#2FA572",
    "warn": "#F57C00",
    "err": "#D32F2F",
    "info": "#3B82F6",
    "idle": ("#8A90B0", "#4A4F6D"),
}

SPIKE_STYLES = {
    "manual": dict(bg=("#F3EEFF", "#211833"), accent=("#7C3AED", "#A78BFA"), icon="map-marker"),
    "vod":    dict(bg=("#E6F7FB", "#0E2530"), accent=("#0891B2", "#22D3EE"), icon="video"),
    "live":   dict(bg=("#FDECEC", "#2A1618"), accent=("#DC2626", "#EF4444"), icon="fire"),
    "ended":  dict(bg=("#FEF3E2", "#271E12"), accent=("#D97706", "#F59E0B"), icon="fire"),
}

DEFAULT_CHAT_COLORS = [
    "#FF0000", "#0000FF", "#00FF7F", "#B22222", "#FF7F50", "#9ACD32",
    "#FF4500", "#2E8B57", "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF",
    "#FF69B4", "#8A2BE2",
]

PLACEHOLDER_COLORS = [
    (125, 92, 255), (0, 160, 220), (236, 72, 153), (245, 158, 11),
    (16, 185, 129), (239, 68, 68), (99, 102, 241), (20, 184, 166),
]

if sys.platform == "win32":
    UI_FONT, MONO_FONT = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    UI_FONT, MONO_FONT = "Helvetica Neue", "Menlo"
else:
    UI_FONT, MONO_FONT = "DejaVu Sans", "DejaVu Sans Mono"

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

_font_cache: dict = {}
_mono_cache: dict = {}
_material_icon_cache: dict = {}


def F(size: int = 12, bold: bool = False) -> ctk.CTkFont:
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = ctk.CTkFont(family=UI_FONT, size=size, weight="bold" if bold else "normal")
    return _font_cache[key]


def MF(size: int) -> ctk.CTkFont:
    if size not in _mono_cache:
        _mono_cache[size] = ctk.CTkFont(family=MONO_FONT, size=size)
    return _mono_cache[size]


def material_icon(name: str, size: int, color: str | tuple[str, str]) -> ctk.CTkImage:
    if isinstance(color, tuple):
        light_color, dark_color = color
    else:
        light_color = dark_color = color
    key = (name, size, light_color, dark_color)
    if key not in _material_icon_cache:
        _material_icon_cache[key] = ctk.CTkImage(
            light_image=MaterialIcon.render_pil(name, size, light_color),
            dark_image=MaterialIcon.render_pil(name, size, dark_color),
            size=(size, size),
        )
    return _material_icon_cache[key]


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
def is_dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


def pc(name: str) -> str:
    v = PALETTE[name]
    return v[1] if is_dark() else v[0]


def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def blend(bg, fg, a: float):
    return tuple(b + (f - b) * a for b, f in zip(bg, fg))


def lerp_rgb(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def readable_chat_color(hex_color: str) -> str:
    try:
        r, g, b = hex_to_rgb(hex_color)
    except Exception:
        return pc("text")
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if is_dark():
        v = max(v, 0.80)
        s = min(s, 0.85)
    else:
        v = min(v, 0.60)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return rgb_to_hex((r * 255, g * 255, b * 255))


def fmt_dur(sec: int) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    return f"{sec // 60}m {sec % 60:02d}s"


# ---------------------------------------------------------------------------
# Settings / streamers persistence
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k in DEFAULT_SETTINGS:
                    if k in raw:
                        data[k] = raw[k]
        except Exception:
            pass
    if data["theme"] not in ("dark", "light"):
        data["theme"] = "dark"
    if data["default_sensitivity"] not in SENSITIVITY_OPTIONS:
        data["default_sensitivity"] = "2.5x (Balanced)"
    try:
        data["volume"] = max(0.0, min(1.0, float(data["volume"])))
        data["chat_font"] = max(10, min(20, int(data["chat_font"])))
    except Exception:
        data["volume"], data["chat_font"] = 0.7, 13
    if not isinstance(data["tabs"], list):
        data["tabs"] = []
    if not isinstance(data["logs_folder"], str):
        data["logs_folder"] = ""
    data["fullscreen"] = bool(data.get("fullscreen", False))
    return data


def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(SETTINGS, f, indent=2)
    except Exception as e:
        print(f"[Settings] Save failed: {e}")


def load_streamers() -> list:
    if os.path.exists(SAVED_STREAMERS_FILE):
        try:
            with open(SAVED_STREAMERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return sorted({s.lower().strip() for s in data if isinstance(s, str) and s.strip()})
        except Exception:
            pass
    return []


def save_streamers(streamers: list):
    try:
        with open(SAVED_STREAMERS_FILE, "w", encoding="utf-8") as f:
            json.dump(streamers, f, indent=2)
    except Exception as e:
        print(f"[Streamers] Save failed: {e}")


def get_log_dir() -> str:
    folder = (SETTINGS.get("logs_folder") or "").strip()
    if folder and os.path.isdir(folder):
        return folder
    return LOG_DIR


def today_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
_spike_sound = None
_audio_ok = False


def init_audio():
    global _spike_sound, _audio_ok
    if not PYGAME_OK:
        print("[Audio] pygame not installed — sound disabled. Run: pip install pygame-ce")
        return
    try:
        pygame.mixer.init()
        _audio_ok = True
        if os.path.exists(SPIKE_SOUND_PATH):
            _spike_sound = pygame.mixer.Sound(SPIKE_SOUND_PATH)
            print(f"[Audio] Loaded {SPIKE_SOUND_PATH}")
        else:
            print(f"[Audio] {SPIKE_SOUND_PATH} not found — add an mp3 to hear notifications")
    except Exception as e:
        print(f"[Audio] Init failed: {e}")


def sound_available() -> bool:
    return _audio_ok and (_spike_sound is not None or os.path.exists(SPIKE_SOUND_PATH))


def play_spike_sound(force: bool = False):
    global _spike_sound
    if not _audio_ok:
        return
    if not force and not SETTINGS.get("sound", True):
        return
    if _spike_sound is None:
        if os.path.exists(SPIKE_SOUND_PATH):
            try:
                _spike_sound = pygame.mixer.Sound(SPIKE_SOUND_PATH)
            except Exception:
                return
        else:
            return
    try:
        _spike_sound.set_volume(float(SETTINGS.get("volume", 0.7)))
        _spike_sound.play()
    except Exception as e:
        print(f"[Audio] Play failed: {e}")


# ---------------------------------------------------------------------------
# IRC helpers
# ---------------------------------------------------------------------------
def parse_irc_message(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None

    tags = {}
    if raw.startswith("@"):
        tag_part, _, raw = raw.partition(" ")
        for pair in tag_part[1:].split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                tags[k] = v

    if raw.startswith("PING"):
        return {"type": "PING", "raw": raw}

    if "PRIVMSG" in raw:
        match = re.search(r":(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #(\w+) :(.*)", raw)
        if match:
            user, channel, message = match.groups()
            display_name = tags.get("display-name", user) or user
            return {
                "type": "PRIVMSG", "tags": tags, "channel": channel,
                "user": display_name, "message": message,
                "timestamp": datetime.now(),
            }
    return None


def get_stream_uptime(channel: str) -> timedelta | None:
    url = DECAPI_UPTIME_URL.format(channel=channel)
    try:
        resp = requests.get(url, timeout=5, headers=HTTP_UA)
        text = resp.text.strip().lower()
        if "offline" in text:
            return None
        h = int(re.search(r"(\d+)\s*hour", text).group(1)) if re.search(r"(\d+)\s*hour", text) else 0
        m = int(re.search(r"(\d+)\s*minute", text).group(1)) if re.search(r"(\d+)\s*minute", text) else 0
        s = int(re.search(r"(\d+)\s*second", text).group(1)) if re.search(r"(\d+)\s*second", text) else 0
        return timedelta(hours=h, minutes=m, seconds=s)
    except Exception:
        return None


def format_timedelta(td: timedelta) -> str:
    s = int(td.total_seconds())
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Spike Detector
# ---------------------------------------------------------------------------
class SpikeDetector:
    def __init__(self, sensitivity_multiplier: float):
        self.sensitivity = sensitivity_multiplier
        self.bucket_counts = collections.deque(maxlen=ROLLING_WINDOW_BUCKETS)
        self.current_bucket_count = 0
        self.recent_messages = collections.deque(maxlen=50)

        self.session_id = 0
        self.in_session = False
        self.session_start = 0.0
        self.session_end = 0.0
        self.session_peak = 0
        self.session_samples = []
        self.last_session_end = 0.0

    def add_message(self, msg: dict):
        self.current_bucket_count += 1
        self.recent_messages.append(msg)

    def roll_bucket(self, bucket_end_time: float):
        count = self.current_bucket_count
        self.current_bucket_count = 0

        avg = (sum(self.bucket_counts) / len(self.bucket_counts)) if len(self.bucket_counts) >= 2 else 0.0
        self.bucket_counts.append(count)

        spike = None

        if len(self.bucket_counts) < 3 or avg == 0:
            if self.in_session:
                self.in_session = False
            return count, avg, None

        bucket_start = bucket_end_time - BUCKET_SECONDS
        is_spike = count >= MIN_SPIKE_THRESHOLD and count > avg * self.sensitivity

        if is_spike:
            if not self.in_session:
                if self.last_session_end > 0 and bucket_start - self.last_session_end < COOLDOWN_SECONDS:
                    return count, avg, None
                self.in_session = True
                self.session_id += 1
                self.session_start = bucket_start
                self.session_peak = count
                self.session_samples = list(self.recent_messages)[-5:]
            else:
                self.session_peak = max(self.session_peak, count)

            self.session_end = bucket_end_time
            intensity = (self.session_peak / avg) if avg > 0 else 0
            spike = {
                "id": self.session_id,
                "status": "active",
                "start": self.session_start,
                "end": self.session_end,
                "peak": self.session_peak,
                "count": count,
                "average": round(avg, 1),
                "intensity": round(intensity, 2),
                "samples": self.session_samples,
            }
        else:
            if self.in_session:
                self.in_session = False
                self.last_session_end = bucket_end_time
                intensity = (self.session_peak / avg) if avg > 0 else 0
                spike = {
                    "id": self.session_id,
                    "status": "ended",
                    "start": self.session_start,
                    "end": self.session_end,
                    "peak": self.session_peak,
                    "count": 0,
                    "average": round(avg, 1),
                    "intensity": round(intensity, 2),
                    "samples": self.session_samples,
                }

        return count, avg, spike


# ---------------------------------------------------------------------------
# VOD Analyzer
# ---------------------------------------------------------------------------
def analyze_vod_chat(
    messages: list,
    sensitivity: float,
    on_progress=None,
    cancel_event: threading.Event | None = None,
) -> list:
    messages = sorted(messages, key=lambda m: m.get("time_in_seconds", 0) or 0)
    detector = SpikeDetector(sensitivity)
    spikes = []
    last_bucket_time = 0.0
    total = len(messages) or 1
    report_every = max(1, total // 100)

    for i, m in enumerate(messages):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("VOD processing cancelled")
        if on_progress and i % report_every == 0:
            on_progress(i / total)
        t = float(m.get("time_in_seconds") or 0)
        while t >= last_bucket_time + BUCKET_SECONDS:
            bucket_end = last_bucket_time + BUCKET_SECONDS
            _, _, spike = detector.roll_bucket(bucket_end)
            if spike:
                spike["start_vod"] = format_timedelta(timedelta(seconds=spike["start"]))
                spike["end_vod"] = format_timedelta(timedelta(seconds=spike["end"]))
                spike["duration"] = int(spike["end"] - spike["start"])
                spike["from_vod"] = True
                spikes.append(spike)
            last_bucket_time = bucket_end
        author = m.get("author") or {}
        if isinstance(author, dict):
            user = author.get("display_name") or author.get("name") or "?"
        else:
            user = str(author) if author else "?"
        detector.add_message({"user": user, "message": m.get("message", ""), "timestamp": datetime.now()})

    bucket_end = last_bucket_time + BUCKET_SECONDS
    _, _, spike = detector.roll_bucket(bucket_end)
    if spike:
        spike["start_vod"] = format_timedelta(timedelta(seconds=spike["start"]))
        spike["end_vod"] = format_timedelta(timedelta(seconds=spike["end"]))
        spike["duration"] = int(spike["end"] - spike["start"])
        spike["from_vod"] = True
        spikes.append(spike)

    if on_progress:
        on_progress(1.0)

    unique = {}
    for s in spikes:
        unique[s["id"]] = s
    return list(unique.values())


def load_vod_json(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        raise ValueError("File is empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if isinstance(data, dict):
        data = data.get("comments") or data.get("messages") or []
    return data


# ---------------------------------------------------------------------------
# Twitch Chat Worker
# ---------------------------------------------------------------------------
class TwitchChatWorker(threading.Thread):
    def __init__(self, channel: str, sensitivity_multiplier: float, gui_queue: queue.Queue, log_dir: str | None = None):
        super().__init__(daemon=True)
        self.channel = channel.lower().strip()
        self.sensitivity = sensitivity_multiplier
        self.gui_queue = gui_queue
        self.running = False
        self.detector = SpikeDetector(sensitivity_multiplier)

        self.total_messages = 0
        self.stream_uptime_at_start = None
        self.session_start = datetime.now()
        self.vod_offset = timedelta(0)

        self.last_message_time = time.time()
        self._was_quiet = False
        self._loop = None
        self._ws = None

        log_dir = log_dir or LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        self.log_dir = log_dir
        date = datetime.now().strftime("%Y-%m-%d")
        self.chat_log_path = os.path.join(log_dir, f"{date}_{self.channel}_chat.csv")
        self.spike_log_path = os.path.join(log_dir, f"{date}_{self.channel}_spikes.csv")
        self._init_log_files()

        self._bucket_lock = threading.Lock()

    def _init_log_files(self):
        chat_header = ["VOD_Time", "User", "Message"]
        if not os.path.exists(self.chat_log_path) or os.path.getsize(self.chat_log_path) == 0:
            with open(self.chat_log_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(chat_header)

        spike_header = ["Start_Time", "End_Time", "Duration_s", "Peak", "Average", "Samples"]
        if not os.path.exists(self.spike_log_path) or os.path.getsize(self.spike_log_path) == 0:
            with open(self.spike_log_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(spike_header)

    def _to_vod_str(self, unix_time: float) -> str:
        elapsed = unix_time - self.session_start.timestamp()
        vod_seconds = self.vod_offset.total_seconds() + elapsed
        return format_timedelta(timedelta(seconds=max(0, vod_seconds)))

    def run(self):
        self.running = True
        asyncio.run(self._async_connect_loop())

    async def _async_connect_loop(self):
        while self.running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                print(f"[Worker/{self.channel}] Connection error: {e}")
                if self.running:
                    self.gui_queue.put(("status", "Reconnecting..."))
                    await asyncio.sleep(3)

    async def _connect_and_listen(self):
        uptime = get_stream_uptime(self.channel)
        if uptime:
            self.stream_uptime_at_start = uptime
            self.vod_offset = uptime
            print(f"[Worker/{self.channel}] Uptime: {format_timedelta(uptime)}")
        else:
            self.vod_offset = timedelta(0)

        justinfan_name = f"justinfan{random.randint(10000, 99999)}"
        print(f"[Worker/{self.channel}] Connecting as {justinfan_name}")

        async with websockets.connect(TWITCH_IRC_URL) as ws:
            self._loop = asyncio.get_running_loop()
            self._ws = ws
            await ws.send("CAP REQ :twitch.tv/tags")
            await ws.send(f"NICK {justinfan_name}")
            await ws.send(f"JOIN #{self.channel}")

            self.last_message_time = time.time()
            self._was_quiet = False
            self.gui_queue.put(("status", "Connected & Live"))
            print(f"[Worker/{self.channel}] Joined #{self.channel}")

            bucket_task = asyncio.create_task(self._bucket_roller())
            watchdog_task = asyncio.create_task(self._offline_watchdog())
            try:
                async for raw_msg in ws:
                    if not self.running:
                        break
                    for line in str(raw_msg).split("\r\n"):
                        parsed = parse_irc_message(line)
                        if not parsed:
                            continue
                        if parsed["type"] == "PING":
                            await ws.send("PONG :tmi.twitch.tv")
                            continue
                        if parsed["type"] == "PRIVMSG":
                            self._handle_privmsg(parsed)
            finally:
                bucket_task.cancel()
                watchdog_task.cancel()
                self._ws = None

    def _handle_privmsg(self, msg: dict):
        self.last_message_time = time.time()
        with self._bucket_lock:
            self.total_messages += 1
            self.detector.add_message(msg)

        session_elapsed = datetime.now() - self.session_start
        vod_str = format_timedelta(self.vod_offset + session_elapsed)

        try:
            with open(self.chat_log_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([vod_str, msg["user"], msg["message"]])
        except Exception as e:
            print(f"[Worker/{self.channel}] Chat log write failed: {e}")

        self.gui_queue.put(("chat", {
            "user": msg["user"],
            "message": msg["message"],
            "color": (msg.get("tags") or {}).get("color", ""),
            "local": msg["timestamp"].strftime("%H:%M:%S"),
            "vod": vod_str,
        }))

    async def _bucket_roller(self):
        while self.running:
            await asyncio.sleep(BUCKET_SECONDS)
            bucket_end = time.time()
            with self._bucket_lock:
                count, avg, spike = self.detector.roll_bucket(bucket_end)
                total = self.total_messages

            self.gui_queue.put(("stats", {
                "current_speed": count,
                "average": round(avg, 1),
                "total_messages": total,
            }))

            if spike:
                spike["start_vod"] = self._to_vod_str(spike["start"])
                spike["end_vod"] = self._to_vod_str(spike["end"])
                spike["start_local"] = datetime.fromtimestamp(spike["start"]).strftime("%H:%M:%S")
                spike["end_local"] = datetime.fromtimestamp(spike["end"]).strftime("%H:%M:%S")
                spike["duration"] = int(spike["end"] - spike["start"])

                if spike["status"] == "ended":
                    try:
                        with open(self.spike_log_path, "a", newline="", encoding="utf-8") as f:
                            samples = "; ".join([f"{m['user']}: {m['message']}" for m in spike["samples"]])
                            csv.writer(f).writerow([
                                spike["start_vod"], spike["end_vod"],
                                spike["duration"], spike["peak"], spike["average"], samples,
                            ])
                    except Exception as e:
                        print(f"[Worker/{self.channel}] Spike log failed: {e}")

                self.gui_queue.put(("spike", spike))
                print(f"[Worker/{self.channel}] Spike {spike['id']} {spike['status']}")

    async def _offline_watchdog(self):
        while self.running:
            await asyncio.sleep(5)
            quiet = (time.time() - self.last_message_time) > NO_MESSAGE_WARN_SECONDS
            if quiet and not self._was_quiet:
                self.gui_queue.put(("status", "No messages — is streamer live?"))
                self._was_quiet = True
            elif not quiet and self._was_quiet:
                self.gui_queue.put(("status", "Connected & Live"))
                self._was_quiet = False

    def stop(self):
        self.running = False
        loop, ws = self._loop, self._ws
        if loop and ws:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Images / avatars
# ---------------------------------------------------------------------------
def _load_font(px: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=px)
    except Exception:
        return ImageFont.load_default()


def circle_crop(img: Image.Image, px: int = AVATAR_PX) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    s = min(w, h)
    left, top = (w - s) // 2, (h - s) // 2
    img = img.crop((left, top, left + s, top + s)).resize((px, px), RESAMPLE)
    mask = Image.new("L", (px * 4, px * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, px * 4 - 1, px * 4 - 1), fill=255)
    img.putalpha(mask.resize((px, px), RESAMPLE))
    return img


def make_placeholder(name: str, px: int = AVATAR_PX) -> Image.Image:
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    color = PLACEHOLDER_COLORS[h % len(PLACEHOLDER_COLORS)]
    big = px * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, big - 1, big - 1), fill=color + (255,))
    letter = (name[:1] or "?").upper()
    font = _load_font(int(big * 0.5))
    try:
        bbox = d.textbbox((0, 0), letter, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((big - tw) / 2 - bbox[0], (big - th) / 2 - bbox[1]), letter, font=font, fill=(255, 255, 255, 255))
    except Exception:
        pass
    return img.resize((px, px), RESAMPLE)


def load_logo(px: int = 256) -> Image.Image:
    """Look for icon.ico, app.ico, peakchat.png, icon.png in order."""
    for name in ("icon.ico", "app.ico", ICON_NAME, "icon.png"):
        path = find_asset(name)
        if path:
            try:
                return Image.open(path).convert("RGBA")
            except Exception as e:
                print(f"[Icon] Could not load {path}: {e}")
    # Fallback: gradient rounded square with "DV"
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    grad = Image.new("RGBA", (px, px))
    gd = ImageDraw.Draw(grad)
    for y in range(px):
        c = lerp_rgb(ACCENT_A_RGB, ACCENT_B_RGB, y / px)
        gd.line([(0, y), (px, y)], fill=tuple(int(v) for v in c) + (255,))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, px - 1, px - 1), radius=px // 4, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    font = _load_font(int(px * 0.46))
    try:
        bbox = d.textbbox((0, 0), "DV", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((px - tw) / 2 - bbox[0], (px - th) / 2 - bbox[1]), "DV", font=font, fill=(255, 255, 255, 255))
    except Exception:
        pass
    return img


class AvatarManager:
    def __init__(self):
        os.makedirs(AVATAR_DIR, exist_ok=True)
        self._pil: dict = {}
        self._ctk: dict = {}
        self._pending: dict = {}
        self._missing: dict = {}
        self._done: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=4)
        self._lock = threading.Lock()

    def _path(self, channel: str) -> str:
        return os.path.join(AVATAR_DIR, f"{channel}.png")

    def _to_ctk(self, channel: str, pil: Image.Image, size: int) -> ctk.CTkImage:
        key = (channel, size)
        if key not in self._ctk:
            self._ctk[key] = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
        return self._ctk[key]

    def _placeholder(self, channel: str, size: int) -> ctk.CTkImage:
        key = ("ph:" + channel, size)
        if key not in self._ctk:
            pil = make_placeholder(channel)
            self._ctk[key] = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
        return self._ctk[key]

    def get(self, channel: str, size: int = 30, callback=None) -> ctk.CTkImage:
        channel = (channel or "").lower().strip()
        if not channel:
            return self._placeholder("?", size)

        pil = self._pil.get(channel)
        if pil is None:
            path = self._path(channel)
            if os.path.exists(path):
                try:
                    pil = Image.open(path).convert("RGBA")
                    pil.load()
                    self._pil[channel] = pil
                    if time.time() - os.path.getmtime(path) > 7 * 86400:
                        self._request(channel, size, None)
                except Exception:
                    pil = None
        if pil is not None:
            return self._to_ctk(channel, pil, size)

        failed_at = self._missing.get(channel)
        if failed_at is None or time.time() - failed_at > 120:
            self._request(channel, size, callback)
        return self._placeholder(channel, size)

    def _request(self, channel: str, size: int, callback):
        with self._lock:
            if channel in self._pending:
                if callback:
                    self._pending[channel].append((callback, size))
                return
            self._pending[channel] = [(callback, size)] if callback else []
        self._pool.submit(self._fetch, channel)

    def _fetch(self, channel: str):
        pil = None
        try:
            r = requests.get(DECAPI_AVATAR_URL.format(channel=channel), timeout=6, headers=HTTP_UA)
            url = r.text.strip()
            if r.ok and url.startswith("http"):
                content = requests.get(url, timeout=8, headers=HTTP_UA).content
                pil = circle_crop(Image.open(io.BytesIO(content)), AVATAR_PX)
                pil.save(self._path(channel))
        except Exception:
            pil = None
        self._done.put((channel, pil))

    def pump(self):
        while True:
            try:
                channel, pil = self._done.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                callbacks = self._pending.pop(channel, [])
            if pil is None:
                if channel not in self._pil:
                    self._missing[channel] = time.time()
                continue
            self._pil[channel] = pil
            for k in [k for k in self._ctk if k[0] == channel]:
                del self._ctk[k]
            for cb, size in callbacks:
                try:
                    cb(self._to_ctk(channel, pil, size))
                except Exception:
                    pass

    def apply(self, label, channel: str, size: int, guard=None):
        def cb(img):
            if guard is not None and not guard():
                return
            try:
                label.configure(image=img)
            except Exception:
                pass
        try:
            label.configure(image=self.get(channel, size, cb))
        except Exception:
            pass

    def image(self, channel: str, size: int, callback=None):
        return self.get(channel, size, callback)


# ---------------------------------------------------------------------------
# Particle background
# ---------------------------------------------------------------------------
class ParticleBackground:
    def __init__(self, app, canvas: tk.Canvas, count: int = 34):
        self.app = app
        self.canvas = canvas
        self.count = count
        self.rng = random.Random()
        self.particles: list = []
        self.enabled = True
        self.t = 0.0
        self.last = time.time()

    def _recolor(self, p):
        bg = hex_to_rgb(pc("bg"))
        boost = 1.0 if is_dark() else 1.7
        for item, opt, alpha in p["items"]:
            color = rgb_to_hex(blend(bg, p["base"], min(1.0, alpha * boost)))
            try:
                self.canvas.itemconfigure(item, **{opt: color})
            except tk.TclError:
                pass

    def recolor(self):
        for p in self.particles:
            self._recolor(p)

    def populate(self):
        self.clear()
        if not self.enabled:
            return
        for _ in range(self.count):
            self.particles.append(self._spawn(True))

    def clear(self):
        for p in self.particles:
            self._destroy(p)
        self.particles.clear()

    def set_enabled(self, on: bool):
        self.enabled = on
        if on:
            self.populate()
        else:
            self.clear()

    def start(self):
        self.last = time.time()
        self._tick()

    def _destroy(self, p):
        for item in {i for i, _, _ in p["items"]}:
            try:
                self.canvas.delete(item)
            except tk.TclError:
                pass

    def _spawn(self, random_y: bool) -> dict:
        c = self.canvas
        s = self.app.scale()
        w = max(c.winfo_width(), 600)
        h = max(c.winfo_height(), 400)
        rng = self.rng
        size = rng.randint(12, 34) * s
        base = lerp_rgb(ACCENT_A_RGB, ACCENT_B_RGB, rng.random())
        cube = rng.random() < 0.55

        p = {
            "x": rng.uniform(0, w),
            "y": rng.uniform(0, h) if random_y else h + size,
            "vx": rng.uniform(-6, 6) * s,
            "vy": -rng.uniform(8, 28) * s,
            "vr": rng.uniform(-18, 18),
            "rot": rng.uniform(0, 90),
            "size": size,
            "phase": rng.uniform(0, math.pi * 2),
            "cube": cube,
            "base": base,
        }
        if cube:
            outer = c.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="", outline="", width=1.4)
            inner = c.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="", outline="", width=1.1)
            p["outer"], p["inner"] = outer, inner
            p["items"] = [(outer, "fill", 0.14), (outer, "outline", 0.75),
                          (inner, "fill", 0.26), (inner, "outline", 0.55)]
        else:
            glow = c.create_oval(0, 0, 1, 1, fill="", outline="", width=3)
            core = c.create_oval(0, 0, 1, 1, fill="", outline="")
            p["glow"], p["core"] = glow, core
            p["items"] = [(glow, "outline", 0.28), (core, "fill", 0.85)]
        for item in {i for i, _, _ in p["items"]}:
            c.tag_lower(item)
        self._recolor(p)
        self._place(p)
        return p

    @staticmethod
    def _square(cx, cy, half, ang):
        co, si = math.cos(ang), math.sin(ang)
        pts = []
        for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half)):
            pts += [cx + dx * co - dy * si, cy + dx * si + dy * co]
        return pts

    def _place(self, p):
        c = self.canvas
        x, y, s = p["x"], p["y"], p["size"]
        try:
            if p["cube"]:
                ang = math.radians(p["rot"])
                c.coords(p["outer"], *self._square(x, y, s / 2, ang))
                off = 0.075 * s
                co, si = math.cos(ang), math.sin(ang)
                ix = x + off * co - off * si
                iy = y + off * si + off * co
                c.coords(p["inner"], *self._square(ix, iy, s * 0.275, ang))
            else:
                r = max(3.0, s / 4) / 2
                c.coords(p["core"], x - r, y - r, x + r, y + r)
                g = r + 3
                c.coords(p["glow"], x - g, y - g, x + g, y + g)
        except tk.TclError:
            pass

    def _tick(self):
        try:
            now = time.time()
            dt = min(0.1, now - self.last)
            self.last = now
            if self.enabled and self.app.state() != "iconic":
                self.t += dt
                w = self.canvas.winfo_width()
                for i, p in enumerate(self.particles):
                    p["y"] += p["vy"] * dt
                    p["x"] += (p["vx"] + math.sin(self.t * 0.6 + p["phase"]) * 4) * dt
                    p["rot"] += p["vr"] * dt
                    if p["y"] < -p["size"] - 6:
                        self._destroy(p)
                        self.particles[i] = self._spawn(False)
                        continue
                    if p["x"] < -p["size"]:
                        p["x"] = w + p["size"]
                    elif p["x"] > w + p["size"]:
                        p["x"] = -p["size"]
                    self._place(p)
            self.app.after(33, self._tick)
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def make_card(parent, radius: int = 14, fg=None, border: bool = True) -> ctk.CTkFrame:
    card = ctk.CTkFrame(
        parent, fg_color=fg or P["surface"], corner_radius=radius,
        border_width=1 if border else 0, border_color=P["stroke"],
    )
    card.pack_propagate(False)
    card.grid_propagate(False)
    return card


def put(widget, x, y, w, h):
    geo = (int(x), int(y), int(w), int(h))
    if getattr(widget, "_last_geo", None) != geo:
        widget.configure(width=geo[2], height=geo[3])
        widget._last_geo = geo
    widget.place(x=geo[0], y=geo[1])


def infer_status_kind(text: str) -> str:
    low = text.lower()
    if "failed" in low or "invalid" in low or "error" in low:
        return "err"
    if "Live" in text:
        return "ok"
    if any(k in text for k in ("No messages", "Reconnecting", "Connecting", "Loading", "Analyzing", "Processing")):
        return "warn"
    if "Saved" in text or "Copied" in text:
        return "ok"
    if "Type a channel" in text or "Pick" in text or "first" in low or "Stop tracking" in text or "No spikes" in text or "No chat" in text:
        return "warn"
    return "idle"


# ---------------------------------------------------------------------------
# Streamer picker
# ---------------------------------------------------------------------------
class StreamerPicker(ctk.CTkFrame):
    ROW_H = 46

    def __init__(self, parent, tab: "StreamTab"):
        super().__init__(parent, width=270, height=44, corner_radius=12,
                         fg_color=P["surface2"], border_width=1, border_color=P["stroke"])
        self.tab = tab
        self.app = tab.app
        self.channel = ""
        self.popup = None
        self.list_frame = None
        self._row_names: list = []
        self._row_buttons: list = []
        self._hl = -1
        self.pack_propagate(False)

        self.avatar = ctk.CTkLabel(self, text="", width=32, height=32)
        self.avatar.pack(side="left", padx=(8, 4), pady=5)

        self.arrow = ctk.CTkButton(
            self, text="", image=material_icon("chevron-down", 16, P["sub"]),
            width=30, height=30, corner_radius=8, fg_color="transparent",
            hover_color=P["surface3"],
            command=self.toggle_popup,
        )
        self.arrow.pack(side="right", padx=(0, 5))

        self.entry = ctk.CTkEntry(
            self, placeholder_text="Choose or type a streamer…", border_width=0,
            fg_color="transparent", text_color=P["text"], placeholder_text_color=P["sub"],
            font=F(13), height=32,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Button-1>", lambda e: self.after(1, self.open_popup))

        self.set_avatar("")

    def set_avatar(self, name: str):
        self.app.avatars.apply(self.avatar, name, 32, guard=lambda: self.channel == name)

    def set_locked(self, locked: bool):
        state = "disabled" if locked else "normal"
        try:
            self.entry.configure(state=state)
            self.arrow.configure(state=state)
        except Exception:
            pass
        if locked:
            self.close_popup()

    def commit(self, name: str | None = None, silent: bool = False) -> bool:
        name = (name if name is not None else self.entry.get()).strip().lower().lstrip("#")
        if not name:
            self.tab.set_status("Type a channel name", "warn")
            return False
        if not TWITCH_NAME_RE.match(name):
            self.tab.set_status("Invalid channel name", "err")
            return False
        self.channel = name
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self.entry.insert(0, name)
        self.set_avatar(name)
        self.tab.on_channel_selected(name, silent=silent)
        return True

    def clear_selection(self):
        self.channel = ""
        self.entry.delete(0, "end")
        self.set_avatar("")
        self.tab.on_channel_selected("", silent=True)

    def _on_return(self, _e=None):
        if self.popup is not None and 0 <= self._hl < len(self._row_names):
            self.select(self._row_names[self._hl])
            return
        if not self.entry.get().strip():
            self.clear_selection()
            self.close_popup()
            return
        if self.commit():
            self.close_popup()

    def _on_key(self, e):
        ks = e.keysym
        if ks in ("Return", "KP_Enter"):
            return
        if ks == "Escape":
            self.close_popup()
            return
        if ks in ("Up", "Down"):
            self.open_popup()
            self._move_hl(1 if ks == "Down" else -1)
            return
        self._hl = -1
        self.open_popup()

    def select(self, name: str):
        if self.commit(name):
            self.close_popup()
            self.app.focus_set()

    def toggle_popup(self):
        if self.popup is not None:
            self.close_popup()
        else:
            self.open_popup()

    def open_popup(self):
        if str(self.entry.cget("state")) == "disabled":
            return
        if self.popup is None:
            if self.app.open_picker is not None and self.app.open_picker is not self:
                self.app.open_picker.close_popup()
            self.popup = ctk.CTkFrame(
                self.app, fg_color=P["surface"], corner_radius=14,
                border_width=1, border_color=P["accent"],
            )
            self.popup.pack_propagate(False)
            self.list_frame = ctk.CTkScrollableFrame(
                self.popup, fg_color="transparent", corner_radius=0,
                scrollbar_button_color=P["surface3"], scrollbar_button_hover_color=P["gray_btn_hover"],
            )
            self.list_frame.pack(fill="both", expand=True, padx=6, pady=6)
            self.app.open_picker = self
        self._refresh_popup()

    def close_popup(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
        self.popup = None
        self.list_frame = None
        self._row_names, self._row_buttons, self._hl = [], [], -1
        if self.app.open_picker is self:
            self.app.open_picker = None

    def on_global_click(self, event):
        w = str(event.widget)
        if self.popup is not None and w.startswith(str(self.popup)):
            return
        if w.startswith(str(self)):
            return
        self.close_popup()

    def _refresh_popup(self):
        if self.popup is None:
            return
        text = self.entry.get().strip().lower().lstrip("#")
        saved = self.app.saved_streamers
        names = [s for s in saved if text in s]
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._row_names, self._row_buttons = [], []

        for name in names:
            self._make_row(name, is_new=False)
        if text and TWITCH_NAME_RE.match(text) and text not in saved:
            self._make_row(text, is_new=True)

        if not self._row_names:
            ctk.CTkLabel(
                self.list_frame, text="No saved streamers yet.\nType a channel name and press Enter.",
                font=F(12), text_color=P["sub"], justify="center",
            ).pack(pady=18)

        rows = max(1, min(len(self._row_names), 6))
        h = rows * self.ROW_H + 24 if self._row_names else 96
        self._position_popup(h)
        self._hl = min(self._hl, len(self._row_names) - 1)
        self._paint_hl()

    def _make_row(self, name: str, is_new: bool):
        row = ctk.CTkFrame(self.list_frame, fg_color="transparent", height=self.ROW_H - 4)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        label = f"Use “{name}”" if is_new else name
        btn = ctk.CTkButton(
            row, text="  " + label, anchor="w", height=self.ROW_H - 6, corner_radius=10,
            fg_color="transparent", hover_color=P["surface3"],
            text_color=P["accent"] if is_new else P["text"], font=F(13, True),
            compound="left", image=None, command=lambda n=name: self.select(n),
        )
        btn.pack(side="left", fill="x", expand=True)

        def cb(img, b=btn):
            try:
                b.configure(image=img)
            except Exception:
                pass
        btn.configure(image=self.app.avatars.get(name, 32, cb))

        if not is_new:
            rm = ctk.CTkButton(
                row, text="", image=material_icon("close", 16, P["sub"]),
                width=28, height=28, corner_radius=8, fg_color="transparent",
                hover_color=P["red"],
                command=lambda n=name: self._remove_saved(n),
            )
            rm.pack(side="right", padx=(2, 4))

        self._row_names.append(name)
        self._row_buttons.append(btn)

    def _remove_saved(self, name: str):
        if name in self.app.saved_streamers:
            self.app.saved_streamers.remove(name)
            save_streamers(self.app.saved_streamers)
        self.app.refresh_saved_state()
        self._refresh_popup()

    def _move_hl(self, delta: int):
        if not self._row_names:
            return
        self._hl = (self._hl + delta) % len(self._row_names)
        self._paint_hl()

    def _paint_hl(self):
        for i, b in enumerate(self._row_buttons):
            try:
                b.configure(fg_color=P["surface3"] if i == self._hl else "transparent")
            except Exception:
                pass

    def _position_popup(self, height: int):
        s = self.app.scale()
        self.update_idletasks()
        x = (self.winfo_rootx() - self.app.winfo_rootx()) / s
        y = (self.winfo_rooty() - self.app.winfo_rooty() + self.winfo_height()) / s + 6
        w = max(self.winfo_width() / s, 300)
        self.popup.configure(width=int(w), height=int(height))
        self.popup.place(x=int(x), y=int(y))
        tk.Misc.lift(self.popup)


# ---------------------------------------------------------------------------
# Tab chip
# ---------------------------------------------------------------------------
class TabChip(ctk.CTkFrame):
    def __init__(self, app, tab_id: str):
        super().__init__(app, width=160, height=40, corner_radius=12,
                         fg_color=P["surface"], border_width=1, border_color=P["stroke"])
        self.app = app
        self.tab_id = tab_id
        self.active = False
        self.flashing = False
        self.channel = ""
        self.title = "New Tab"
        self._width = 160
        self.pack_propagate(False)

        self.close_btn = ctk.CTkButton(
            self, text="", image=material_icon("close", 16, P["sub"]),
            width=24, height=24, corner_radius=8, fg_color="transparent",
            hover_color=P["red"],
            command=lambda: app.close_tab(tab_id),
        )
        self.close_btn.pack(side="right", padx=(0, 6))

        self.avatar = ctk.CTkLabel(self, text="", width=26, height=26)
        self.avatar.pack(side="left", padx=(8, 4))
        self.dot = ctk.CTkLabel(
            self, text="", image=material_icon("circle", 8, P["sub"]), width=10,
        )
        self.dot.pack(side="left", padx=(0, 4))
        self.label = ctk.CTkLabel(self, text="New Tab", font=F(12, True), anchor="w", text_color=P["sub"])
        self.label.pack(side="left", fill="x", expand=True)

        for w in (self, self.avatar, self.label, self.dot):
            w.bind("<Button-1>", lambda e: app.activate_tab(tab_id))

        self.set_channel("")
        self._style()

    def set_channel(self, channel: str):
        self.channel = channel
        self.title = channel or "New Tab"
        if channel:
            self.app.avatars.apply(self.avatar, channel, 26, guard=lambda: self.channel == channel)
        else:
            self.avatar.configure(image=self.app.logo_image(26))
        self._update_text()

    def set_width(self, w: int):
        self._width = int(w)
        self._update_text()

    def _update_text(self):
        maxc = max(3, (self._width - 90) // 8)
        t = self.title if len(self.title) <= maxc else self.title[:maxc - 1] + "…"
        self.label.configure(text=t)

    def set_state_dot(self, kind: str):
        colors = {"ok": "#22C55E", "warn": "#F59E0B", "err": "#EF4444"}
        self.dot.configure(image=material_icon("circle", 8, colors.get(kind, P["sub"])))

    def set_active(self, active: bool):
        self.active = active
        self._style()

    def flash(self):
        if self.active or self.flashing:
            return
        self.flashing = True
        self._style()
        self.after(4000, self._end_flash)

    def _end_flash(self):
        self.flashing = False
        try:
            self._style()
        except Exception:
            pass

    def _style(self):
        if self.flashing:
            self.configure(fg_color="#7F1D1D", border_color="#EF4444")
            self.label.configure(text_color="#FFFFFF")
        elif self.active:
            self.configure(fg_color=P["accent"], border_color=P["accent_hover"])
            self.label.configure(text_color="#FFFFFF")
        else:
            self.configure(fg_color=P["surface"], border_color=P["stroke"])
            self.label.configure(text_color=P["sub"])


# ---------------------------------------------------------------------------
# StreamTab
# ---------------------------------------------------------------------------
class StreamTab:
    STAT_H = 92
    FOOT_H = 40
    M = 18
    G = 12

    def __init__(self, app, tab_id: str, sensitivity_label: str):
        self.app = app
        self.tab_id = tab_id
        self.sens_label = sensitivity_label
        self.worker: TwitchChatWorker | None = None
        self.queue: queue.Queue = queue.Queue()
        self.spikes: dict = {}
        self.spike_widgets: dict = {}
        self._deleted: set = set()
        self._chat_buffer: list = []
        self._chat_tags: dict = {}
        self.history = collections.deque(maxlen=SPARK_SLOTS)
        self.visible = False
        self._closed = False
        self._jump_shown = False
        self._connected = False
        self._connection_state = "idle"
        self._vod_download_active = False
        self._vod_cancel_event: threading.Event | None = None
        self._vod_job_id: str | None = None
        self._vod_progress_dialog = None
        self.cards: list = []
        self.floaters: list = []
        self._import_menu = None

        self._build_controls()
        self._build_stats()
        self._build_chat()
        self._build_spikes()
        self._build_footer()
        self.apply_chat_font()
        self.on_theme_changed()
        self._poll_job = self.app.after(100, self._poll)

    # --- Build --------------------------------------------------------------
    def _build_controls(self):
        c = make_card(self.app)
        self.controls = c
        self.cards.append(c)

        self.picker = StreamerPicker(c, self)
        self.picker.pack(side="left", padx=(14, 8), pady=9)

        self.save_btn = ctk.CTkButton(
            c, text="Save", image=material_icon("bookmark-outline", 16, P["text"]),
            compound="left", width=90, height=40, corner_radius=10,
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            font=F(12, True), command=self._toggle_saved,
        )
        self.save_btn.pack(side="left", padx=(0, 14), pady=9)

        self.sensitivity_var = ctk.StringVar(value=self.sens_label)
        self.sensitivity_menu = ctk.CTkOptionMenu(
            c, values=list(SENSITIVITY_OPTIONS.keys()), variable=self.sensitivity_var,
            width=180, height=38, corner_radius=10, font=F(12), dropdown_font=F(12),
            fg_color=P["surface2"], button_color=P["surface3"], button_hover_color=P["gray_btn_hover"],
            text_color=P["text"], dropdown_fg_color=P["surface"], dropdown_hover_color=P["surface3"],
            dropdown_text_color=P["text"], command=lambda _v: self.app.save_state(),
        )
        self.sensitivity_menu.pack(side="left", pady=9)

        self.toggle_btn = ctk.CTkButton(
            c, text="Start", image=material_icon("play", 18, "#FFFFFF"),
            compound="left", width=130, height=40, corner_radius=10,
            fg_color=P["green"], hover_color=P["green_hover"], text_color="#FFFFFF",
            font=F(14, True), command=self._toggle,
        )
        self.toggle_btn.pack(side="left", padx=(18, 8), pady=9)

        self.status_label = ctk.CTkLabel(
            c, text="Disconnected", image=material_icon("circle", 10, STATUS["idle"]),
            compound="left", width=205, height=30, corner_radius=15,
            fg_color=STATUS["idle"], text_color="#FFFFFF", font=F(12, True),
        )
        self.status_label.pack(side="left", pady=9)

        self.mark_btn = ctk.CTkButton(
            c, text="Mark", image=material_icon("map-marker", 18, "#FFFFFF"),
            compound="left", width=92, height=40, corner_radius=10,
            fg_color=P["accent"], hover_color=P["accent_hover"], text_color="#FFFFFF",
            font=F(12, True), command=self._manual_mark,
        )
        self.mark_btn.pack(side="right", padx=14, pady=9)

    def _build_stats(self):
        self.stat_cards = []
        self.stat_labels = {}
        items = [
            ("current_speed", "SPEED", "msgs / 10s", "0", P["cyan"]),
            ("average", "NORMAL AVG", "msgs / 10s", "0.0", P["text"]),
            ("total_spikes", "SPIKES", "detected", "0", P["amber"]),
            ("total_messages", "MESSAGES", "this session", "0", P["text"]),
        ]
        for key, title, unit, default, color in items:
            card = make_card(self.app)
            ctk.CTkLabel(card, text=title, font=F(10, True), text_color=P["sub"]).pack(anchor="w", padx=16, pady=(12, 0))
            val = ctk.CTkLabel(card, text=default, font=F(26, True), text_color=color)
            val.pack(anchor="w", padx=16)
            ctk.CTkLabel(card, text=unit, font=F(10), text_color=P["sub"]).pack(anchor="w", padx=16, pady=(0, 6))
            self.stat_labels[key] = val
            self.stat_cards.append(card)
            self.cards.append(card)

        spark = make_card(self.app)
        head = ctk.CTkFrame(spark, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkLabel(head, text="CHAT ACTIVITY", font=F(10, True), text_color=P["sub"]).pack(side="left")
        ctk.CTkLabel(head, text="last 6 min  ·  red = spike", font=F(10), text_color=P["sub"]).pack(side="right")
        self.spark = tk.Canvas(spark, highlightthickness=0, bd=0, height=40)
        self.spark.pack(fill="both", expand=True, padx=14, pady=(4, 10))
        self.spark.bind("<Configure>", lambda e: self._draw_spark())
        self.spark_card = spark
        self.cards.append(spark)

    def _build_chat(self):
        card = make_card(self.app)
        self.chat_card = card
        self.cards.append(card)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 0), side="top")
        ctk.CTkLabel(
            header, text="Live Chat", image=material_icon("chat", 18, P["text"]),
            compound="left", font=F(15, True), text_color=P["text"],
        ).pack(side="left")
        self.clear_chat_btn = ctk.CTkButton(
            header, text="Clear", width=56, height=26, corner_radius=8, font=F(11),
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            command=self._clear_chat,
        )
        self.clear_chat_btn.pack(side="right")
        self.jump_btn = ctk.CTkButton(
            header, text="Latest", image=material_icon("arrow-down", 14, "#FFFFFF"),
            compound="left", width=84, height=26, corner_radius=8, font=F(11, True),
            fg_color=P["accent"], hover_color=P["accent_hover"], text_color="#FFFFFF",
            command=self._jump_latest,
        )
        self._jump_header = header

        # Footer with Save Chat button (bottom-left inside this panel)
        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.pack(fill="x", padx=10, pady=(0, 10), side="bottom")
        self.foot_chat = ctk.CTkButton(
            footer, text="Save Chat (.csv)", image=material_icon("file-download", 16, "#FFFFFF"),
            compound="left", height=32, width=160, corner_radius=8,
            font=F(12, True), fg_color="#3B82F6", hover_color="#2563EB",
            text_color="#FFFFFF", command=self._export_chat,
        )
        self.foot_chat.pack(side="left")
        self.chat_footer = footer

        self.chat_box = ctk.CTkTextbox(
            card, wrap="word", corner_radius=10, border_width=0, fg_color=P["surface2"],
            text_color=P["text"], scrollbar_button_color=P["surface3"],
            scrollbar_button_hover_color=P["gray_btn_hover"],
        )
        self.chat_box.pack(fill="both", expand=True, padx=10, pady=(10, 6), side="top")
        self.chat_box.configure(state="disabled")

    def _build_spikes(self):
        card = make_card(self.app)
        self.spike_card = card
        self.cards.append(card)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 0), side="top")
        ctk.CTkLabel(
            header, text="Spike Detector", image=material_icon("fire", 18, P["text"]),
            compound="left", font=F(15, True), text_color=P["text"],
        ).pack(side="left")
        self.import_btn = ctk.CTkButton(
            header, text="Import VOD", image=material_icon("video", 14, "#FFFFFF"),
            compound="left", width=108, height=26, corner_radius=8, font=F(11, True),
            fg_color=P["cyan"], hover_color=("#0E7490", "#0891B2"), text_color="#FFFFFF",
            command=self._show_import_menu,
        )
        self.import_btn.pack(side="right")
        ctk.CTkButton(
            header, text="Clear", width=56, height=26, corner_radius=8, font=F(11),
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            command=self._clear_spikes_click,
        ).pack(side="right", padx=(0, 6))

        # Footer with Save Spikes button (bottom-left inside this panel)
        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.pack(fill="x", padx=10, pady=(0, 10), side="bottom")
        self.foot_save = ctk.CTkButton(
            footer, text="Save Spikes (.csv)", image=material_icon("file-download", 16, "#FFFFFF"),
            compound="left", height=32, width=160, corner_radius=8,
            font=F(12, True), fg_color="#8B5CF6", hover_color="#7C3AED",
            text_color="#FFFFFF", command=self._export_spikes,
        )
        self.foot_save.pack(side="left")
        self.spike_footer = footer

        self.spike_feed = ctk.CTkScrollableFrame(
            card, corner_radius=10, fg_color=P["surface2"],
            scrollbar_button_color=P["surface3"], scrollbar_button_hover_color=P["gray_btn_hover"],
        )
        self.spike_feed.pack(fill="both", expand=True, padx=10, pady=(10, 6), side="top")

        self.empty_label = ctk.CTkLabel(
            self.spike_feed, text="No spikes yet\nStart tracking, or import a VOD chat file.",
            image=material_icon("fire", 34, P["sub"]), compound="top",
            font=F(13), text_color=P["sub"], justify="center",
        )
        self.empty_label.pack(pady=44)
        self._empty_shown = True

    def _build_footer(self):
        # Only "Open Logs" remains in the global footer.
        self.open_logs_btn = ctk.CTkButton(
            self.app, text="Open Logs Folder", image=material_icon("folder-open", 18, "#FFFFFF"),
            compound="left", width=160, height=34, corner_radius=10,
            fg_color=P["accent"], hover_color=P["accent_hover"], text_color="#FFFFFF",
            font=F(12, True), command=self._open_logs,
        )
        self.floaters = [self.open_logs_btn]

    # --- Layout / visibility -----------------------------------------------
    def layout(self, W: int, H: int, top: int):
        M, G = self.M, self.G
        avail = W - 2 * M

        put(self.controls, M, top, avail, 62)
        y = top + 62 + G

        units = [1, 1, 1, 1, 2.8]
        unit = (avail - G * 4) / sum(units)
        x = M
        for card, u in zip(self.stat_cards + [self.spark_card], units):
            w = unit * u
            put(card, x, y, w, self.STAT_H)
            x += w + G
        y += self.STAT_H + G

        foot_y = H - M - self.FOOT_H
        panels_h = max(220, foot_y - G - y)
        chat_w = int((avail - G) * 0.5)
        put(self.chat_card, M, y, chat_w, panels_h)
        put(self.spike_card, M + chat_w + G, y, avail - chat_w - G, panels_h)

        self.open_logs_btn.place(x=W - M - 160, y=foot_y)

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False
        self.picker.close_popup()
        self._close_import_menu()
        for w in self.cards + self.floaters:
            w.place_forget()

    def destroy(self):
        if self._vod_download_active:
            self._confirm_vod_cancel()
            self._finish_vod_download()
        self._closed = True
        try:
            self.app.after_cancel(self._poll_job)
        except Exception:
            pass
        self.picker.close_popup()
        self._close_import_menu()
        if self.worker:
            self.worker.stop()
            self.worker = None
        for w in self.cards + self.floaters:
            try:
                w.destroy()
            except Exception:
                pass

    # --- Theme -------------------------------------------------------------
    def on_theme_changed(self):
        try:
            self.spark.configure(bg=pc("surface"))
        except Exception:
            pass
        self._draw_spark()
        try:
            self.chat_box.tag_config("ts", foreground=pc("sub"))
            for tag, base in self._chat_tags.items():
                self.chat_box.tag_config(tag, foreground=readable_chat_color(base))
        except Exception:
            pass
        self._refresh_save_btn()
        self._update_toggle_button()
        if self._vod_progress_dialog is not None:
            self._vod_progress_dialog.on_theme_changed()

    def apply_chat_font(self):
        self.chat_box.configure(font=MF(int(SETTINGS.get("chat_font", 13))))

    # --- Status ------------------------------------------------------------
    def set_status(self, text: str, kind: str | None = None):
        kind = kind or infer_status_kind(text)
        shown = "Quiet — is streamer live?" if text.startswith("No messages") else text
        if len(shown) > 30:
            shown = shown[:29] + "…"
        self.status_label.configure(
            text=shown, image=material_icon("circle", 10, STATUS[kind]),
            fg_color=STATUS[kind],
        )
        if text == "Connected & Live":
            self._connected = True
            self._connection_state = "connected"
        elif text.startswith("Connecting"):
            self._connected = False
            self._connection_state = "connecting"
        elif text.startswith("Reconnecting"):
            self._connected = False
            self._connection_state = "reconnecting"
        elif text == "Disconnected":
            self._connected = False
            self._connection_state = "idle"
        self._update_toggle_button()
        try:
            self.app.set_chip_state(self.tab_id, kind if self.worker else "idle")
        except Exception:
            pass

    # --- Handlers ----------------------------------------------------------
    def on_channel_selected(self, name: str, silent: bool = False):
        self.app.rename_tab(self.tab_id, name)
        self._refresh_save_btn()
        if name and not silent:
            self.set_status(f"Selected {name}", "info")
        self.app.save_state()

    def _refresh_save_btn(self):
        ch = self.picker.channel
        if ch and ch in self.app.saved_streamers:
            self.save_btn.configure(
                text="Saved", image=material_icon("bookmark", 16, P["amber"]),
                text_color=P["amber"],
            )
        else:
            self.save_btn.configure(
                text="Save", image=material_icon("bookmark-outline", 16, P["text"]),
                text_color=P["text"],
            )

    def _toggle_saved(self):
        if not self.picker.channel:
            if not self.picker.commit():
                return
        name = self.picker.channel
        if name in self.app.saved_streamers:
            self.app.saved_streamers.remove(name)
            self.set_status(f"Removed {name}", "info")
        else:
            self.app.saved_streamers.append(name)
            self.app.saved_streamers.sort()
            self.set_status(f"Saved {name}", "ok")
        save_streamers(self.app.saved_streamers)
        self.app.refresh_saved_state()

    def _toggle(self):
        if self._vod_download_active:
            return
        if self.worker is not None:
            if self._connected:
                self._stop()
            return
        self._start()

    def _update_toggle_button(self):
        if self._vod_download_active:
            self.toggle_btn.configure(
                text="Downloading", image=material_icon("cloud-download", 18, P["sub"]),
                state="disabled", fg_color=P["gray_btn"], hover_color=P["gray_btn"],
                text_color=P["sub"],
            )
        elif self.worker is not None and not self._connected:
            label = "Reconnecting" if self._connection_state == "reconnecting" else "Connecting"
            self.toggle_btn.configure(
                text=label, image=None, state="disabled",
                fg_color=P["gray_btn"], hover_color=P["gray_btn"], text_color=P["sub"],
            )
        elif self.worker is not None:
            self.toggle_btn.configure(
                text="Stop", image=material_icon("stop", 18, "#FFFFFF"),
                state="normal", fg_color=P["red"], hover_color=P["red_hover"],
                text_color="#FFFFFF",
            )
        else:
            self.toggle_btn.configure(
                text="Start", image=material_icon("play", 18, "#FFFFFF"),
                state="normal", fg_color=P["green"], hover_color=P["green_hover"],
                text_color="#FFFFFF",
            )

    def _start(self):
        if self._vod_download_active or self.worker is not None:
            return
        if not self.picker.commit(self.picker.entry.get() if self.picker.entry.get().strip() else None):
            return
        channel = self.picker.channel
        if channel not in self.app.saved_streamers:
            self.app.saved_streamers.append(channel)
            self.app.saved_streamers.sort()
            save_streamers(self.app.saved_streamers)
            self.app.refresh_saved_state()

        sensitivity = SENSITIVITY_OPTIONS[self.sensitivity_var.get()]

        self._reset_spikes()
        self.history.clear()
        self._draw_spark()
        for k in ("current_speed", "average", "total_messages"):
            self.stat_labels[k].configure(text="0" if k != "average" else "0.0")

        self.queue = queue.Queue()
        self.worker = TwitchChatWorker(channel, sensitivity, self.queue, log_dir=get_log_dir())
        self.worker.start()

        self.picker.set_locked(True)
        self.set_status("Connecting...", "warn")
        self.app.save_state()

    def _stop(self):
        if self.worker is not None and not self._connected:
            return
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.picker.set_locked(False)
        self.set_status("Disconnected", "idle")

    def _manual_mark(self):
        if not self.worker or not self.worker.running:
            self.set_status("Start tracking first", "warn")
            return
        now = time.time()
        vod_str = self.worker._to_vod_str(now)
        payload = {
            "id": f"manual-{uuid.uuid4().hex[:6]}",
            "status": "ended",
            "start": now, "end": now,
            "peak": 0, "average": 0, "intensity": 0,
            "samples": [],
            "start_vod": vod_str,
            "end_vod": vod_str,
            "start_local": datetime.now().strftime("%H:%M:%S"),
            "end_local": datetime.now().strftime("%H:%M:%S"),
            "duration": 0,
            "manual": True,
        }
        self.spikes[payload["id"]] = payload
        self._create_card(payload, top=True)
        self._update_spike_count()

    def _clear_chat(self):
        self.chat_box.configure(state="normal")
        self.chat_box.delete("1.0", "end")
        self.chat_box.configure(state="disabled")

    def _jump_latest(self):
        self.chat_box.see("end")
        self._set_jump(False)

    def _set_jump(self, show: bool):
        if show and not self._jump_shown:
            self.jump_btn.pack(side="right", padx=(0, 6))
            self._jump_shown = True
        elif not show and self._jump_shown:
            self.jump_btn.pack_forget()
            self._jump_shown = False

    def _clear_spikes_click(self):
        self._reset_spikes()

    # --- Import VOD (dropdown + 2 options) ---------------------------------
    def _show_import_menu(self):
        # Toggle off if already open
        if self._import_menu is not None:
            self._close_import_menu()
            return

        menu = ctk.CTkToplevel(self.app, fg_color=P["surface"])
        menu.withdraw()
        menu.overrideredirect(True)
        menu.transient(self.app)
        panel = ctk.CTkFrame(
            menu, fg_color=P["surface"], corner_radius=12,
            border_width=1, border_color=P["accent"],
        )
        panel.pack(fill="both", expand=True)

        def pick_local():
            self._close_import_menu()
            self._import_vod_from_file()

        def pick_download():
            self._close_import_menu()
            self._import_vod_from_url()

        ctk.CTkButton(
            panel, text="Local file", image=material_icon("folder-open", 16, P["text"]),
            compound="left", anchor="w", height=36, width=210,
            fg_color="transparent", hover_color=P["surface3"], text_color=P["text"],
            font=F(12, True), command=pick_local,
        ).pack(fill="x", padx=6, pady=(6, 2))

        ctk.CTkButton(
            panel, text="Chat Download (URL)",
            image=material_icon("cloud-download", 16, P["text"]),
            compound="left", anchor="w", height=36, width=210,
            fg_color="transparent", hover_color=P["surface3"], text_color=P["text"],
            font=F(12, True), command=pick_download,
        ).pack(fill="x", padx=6, pady=(2, 6))

        self.app.update_idletasks()
        btn = self.import_btn
        scale = self.app.scale()
        width, height = 230, 94
        width_px, height_px = round(width * scale), round(height * scale)
        x = btn.winfo_rootx() + btn.winfo_width() - width_px
        y = btn.winfo_rooty() + btn.winfo_height() + round(4 * scale)
        x = max(4, min(x, self.app.winfo_screenwidth() - width_px - 4))
        if y + height_px > self.app.winfo_screenheight() - 4:
            y = btn.winfo_rooty() - height_px - round(4 * scale)
        y = max(4, y)
        menu.geometry(f"{width}x{height}+{x}+{y}")
        menu.bind("<Escape>", lambda _event: self._close_import_menu())
        self._import_menu = menu
        menu.deiconify()
        menu.lift()
        menu.focus_force()

    def _close_import_menu(self):
        if self._import_menu is not None:
            try:
                self._import_menu.destroy()
            except Exception:
                pass
            self._import_menu = None

    def _import_vod_from_file(self):
        if self.worker is not None:
            self.set_status("Stop tracking before importing VOD", "warn")
            return
        if self._vod_download_active:
            self.set_status("VOD import already in progress", "warn")
            return
        path = filedialog.askopenfilename(
            title="Select chat-downloader JSON",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            parent=self.app,
        )
        if not path:
            return
        self.set_status("Loading VOD...", "warn")
        sensitivity = SENSITIVITY_OPTIONS[self.sensitivity_var.get()]
        threading.Thread(
            target=self._process_vod, args=(path, sensitivity, self.queue), daemon=True
        ).start()

    def _import_vod_from_url(self):
        if self.worker is not None:
            self.set_status("Stop tracking before importing VOD", "warn")
            return
        if self._vod_download_active:
            self.set_status("VOD import already in progress", "warn")
            return
        dlg = VODURLDialog(self.app)
        url = dlg.get_input()
        if not url:
            return
        url = url.strip()
        if not url.startswith("http"):
            self.set_status("Invalid URL", "err")
            return
        sensitivity = SENSITIVITY_OPTIONS[self.sensitivity_var.get()]
        self._vod_download_active = True
        self._vod_cancel_event = threading.Event()
        self._vod_job_id = uuid.uuid4().hex
        self._update_toggle_button()
        self._vod_progress_dialog = VODProgressDialog(
            self.app, self._request_vod_cancel,
        )
        self.set_status("Downloading chat...", "warn")
        threading.Thread(
            target=self._download_chat_from_url,
            args=(url, sensitivity, self.queue, self._vod_cancel_event, self._vod_job_id),
            daemon=True,
        ).start()

    def _request_vod_cancel(self):
        if not self._vod_download_active or self._vod_cancel_event is None:
            return
        ThemedConfirmationDialog(
            self.app,
            title="Cancel download?",
            message="Stop downloading and analyzing this VOD?",
            confirm_text="Cancel download",
            cancel_text="Keep downloading",
            on_confirm=self._confirm_vod_cancel,
        )

    def _confirm_vod_cancel(self):
        event = self._vod_cancel_event
        if event is None:
            return
        event.set()
        self._finish_vod_download()
        self.set_status("VOD import canceled", "warn")

    def _finish_vod_download(self):
        self._vod_download_active = False
        self._vod_cancel_event = None
        self._vod_job_id = None
        if self._vod_progress_dialog is not None:
            try:
                self._vod_progress_dialog.destroy()
            except tk.TclError:
                pass
            self._vod_progress_dialog = None
        self._update_toggle_button()

    def _download_chat_from_url(
        self,
        url: str,
        sensitivity: float,
        q: queue.Queue,
        cancel_event: threading.Event,
        job_id: str,
    ):
        def emit(message_type, payload=None):
            q.put((message_type, (job_id, payload)))

        downloader = None
        try:
            from chat_downloader import ChatDownloader
        except ImportError:
            emit("vod_download_error", "chat-downloader is not installed")
            return
        try:
            class CancelableChatDownloader(ChatDownloader):
                def create_session(self, chat_downloader_class, overwrite=False):
                    session = super().create_session(chat_downloader_class, overwrite)
                    if not getattr(session, "_peakchat_request_wrapped", False):
                        original_request = session.session.request

                        def request_with_timeout(method, request_url, **kwargs):
                            if cancel_event.is_set():
                                raise InterruptedError("VOD download canceled")
                            if kwargs.get("timeout") is None:
                                kwargs["timeout"] = (5, 10)
                            return original_request(method, request_url, **kwargs)

                        session.session.request = request_with_timeout
                        session._peakchat_request_wrapped = True
                    return session

            downloader = CancelableChatDownloader()
            if cancel_event.is_set():
                emit("vod_download_cancelled")
                return
            chat = downloader.get_chat(
                url, retry_timeout=0.5, interruptible_retry=False,
            )
            if cancel_event.is_set():
                emit("vod_download_cancelled")
                return
            duration = getattr(chat, "duration", None)
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                duration = 0

            messages = []
            furthest_time = 0.0
            last_percent = 1
            emit("vod_download_progress", last_percent)
            for msg in chat:
                if cancel_event.is_set():
                    emit("vod_download_cancelled")
                    return
                messages.append(msg)
                if duration > 0:
                    try:
                        furthest_time = max(
                            furthest_time, float(msg.get("time_in_seconds") or 0),
                        )
                    except (TypeError, ValueError):
                        pass
                    percent = min(99, max(1, int(furthest_time * 100 / duration)))
                    if percent > last_percent:
                        last_percent = percent
                        emit("vod_download_progress", percent)

            if cancel_event.is_set():
                emit("vod_download_cancelled")
                return
            if not messages:
                emit("vod_download_error", "No chat messages were downloaded")
                return

            emit("vod_download_progress", 100)
            emit("vod_analysis_progress", 1)
            spikes = analyze_vod_chat(
                messages, sensitivity,
                on_progress=lambda p: emit(
                    "vod_analysis_progress", min(100, max(1, int(p * 100))),
                ),
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                emit("vod_download_cancelled")
                return
            emit("vod_done", {"spikes": spikes, "total": len(messages)})
        except Exception as e:
            if cancel_event.is_set():
                emit("vod_download_cancelled")
            else:
                emit("vod_download_error", str(e)[:160])
        finally:
            if downloader is not None:
                try:
                    downloader.close()
                except Exception as e:
                    print(f"[VOD] Could not close the downloader: {e}")

    def _process_vod(self, path, sensitivity, q):
        try:
            data = load_vod_json(path)
            if not data:
                q.put(("status", "No messages in file"))
                return
            q.put(("status", f"Analyzing {len(data):,} messages..."))
            spikes = analyze_vod_chat(
                data, sensitivity,
                on_progress=lambda p: q.put(("vod_progress", p)),
            )
            q.put(("vod_file_done", {"spikes": spikes, "total": len(data)}))
        except Exception as e:
            q.put(("status", f"VOD failed: {e}"))

    # --- Queue polling ------------------------------------------------------
    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                try:
                    msg_type, payload = self.queue.get_nowait()
                except queue.Empty:
                    break

                if msg_type == "status":
                    self.set_status(payload)
                elif msg_type == "stats":
                    self.stat_labels["current_speed"].configure(text=str(payload["current_speed"]))
                    self.stat_labels["average"].configure(text=str(payload["average"]))
                    self.stat_labels["total_messages"].configure(text=f"{payload['total_messages']:,}")
                    self.history.append((payload["current_speed"], payload["average"]))
                    self._draw_spark()
                elif msg_type == "spike":
                    self._handle_spike(payload)
                elif msg_type == "chat":
                    self._chat_buffer.append(payload)
                elif msg_type == "vod_progress":
                    self.set_status(f"Processing... {int(payload * 100)}%", "warn")
                elif msg_type in {
                    "vod_download_progress",
                    "vod_analysis_progress",
                    "vod_download_cancelled",
                    "vod_download_error",
                    "vod_done",
                }:
                    job_id, event_payload = payload
                    if job_id != self._vod_job_id:
                        continue
                    if msg_type == "vod_download_progress":
                        if self._vod_progress_dialog is not None:
                            self._vod_progress_dialog.set_progress(event_payload, "Downloading")
                    elif msg_type == "vod_analysis_progress":
                        if self._vod_progress_dialog is not None:
                            self._vod_progress_dialog.set_progress(event_payload, "Analyzing")
                    elif msg_type == "vod_download_cancelled":
                        self._finish_vod_download()
                        self.set_status("VOD import canceled", "warn")
                    elif msg_type == "vod_download_error":
                        self._finish_vod_download()
                        self.set_status(f"VOD failed: {event_payload}", "err")
                    else:
                        canceled = (
                            self._vod_cancel_event is not None
                            and self._vod_cancel_event.is_set()
                        )
                        self._finish_vod_download()
                        if canceled:
                            self.set_status("VOD import canceled", "warn")
                        else:
                            self._handle_vod_done(event_payload)
                elif msg_type == "vod_file_done":
                    self._handle_vod_done(payload)

            if self._chat_buffer:
                self._flush_chat()
            self._update_jump_btn()
        except tk.TclError:
            return
        self._poll_job = self.app.after(100, self._poll)

    # --- Chat ---------------------------------------------------------------
    def _user_tag(self, m: dict) -> str:
        color = m.get("color") or ""
        if not HEX_RE.match(color):
            idx = int(hashlib.md5(m["user"].lower().encode("utf-8")).hexdigest(), 16) % len(DEFAULT_CHAT_COLORS)
            color = DEFAULT_CHAT_COLORS[idx]
        tag = "c" + color[1:].lower()
        if tag not in self._chat_tags:
            self._chat_tags[tag] = color
            self.chat_box.tag_config(tag, foreground=readable_chat_color(color))
        return tag

    def _flush_chat(self):
        buf = self._chat_buffer[-300:]
        self._chat_buffer.clear()
        tb = self.chat_box
        try:
            at_bottom = tb.yview()[1] >= 0.985
        except Exception:
            at_bottom = True
        tb.configure(state="normal")
        for m in buf:
            tag = self._user_tag(m)
            tb.insert("end", f"{m['local']}  ", "ts")
            tb.insert("end", m["user"], tag)
            tb.insert("end", f":  {m['message']}\n")

        total = int(tb.index("end-1c").split(".")[0])
        if total > CHAT_MAX_LINES:
            tb.delete("1.0", f"{total - CHAT_MAX_LINES}.0")

        if at_bottom:
            tb.see("end")
        tb.configure(state="disabled")

    def _update_jump_btn(self):
        try:
            self._set_jump(self.chat_box.yview()[1] < 0.985)
        except Exception:
            pass

    # --- Sparkline ----------------------------------------------------------
    def _draw_spark(self):
        cv = self.spark
        try:
            cv.delete("all")
            w, h = cv.winfo_width(), cv.winfo_height()
        except tk.TclError:
            return
        if w < 30 or h < 12:
            return
        data = list(self.history)
        sens = SENSITIVITY_OPTIONS.get(self.sensitivity_var.get(), 2.5)
        mx = max([c for c, _ in data] + [a for _, a in data] + [5]) * 1.15
        slot_w = w / SPARK_SLOTS
        base = pc("surface3")
        cv.create_line(0, h - 1, w, h - 1, fill=base)
        normal = pc("accent")
        hot = "#EF4444"
        for i, (count, avg) in enumerate(data):
            slot = SPARK_SLOTS - len(data) + i
            bar_h = max(2, count / mx * (h - 6))
            is_spike = count >= MIN_SPIKE_THRESHOLD and avg > 0 and count > avg * sens
            cv.create_rectangle(
                slot * slot_w + 1.5, h - bar_h - 1, (slot + 1) * slot_w - 1.5, h - 1,
                fill=hot if is_spike else normal, outline="",
            )
        if data and data[-1][1] > 0:
            y = h - 1 - data[-1][1] / mx * (h - 6)
            cv.create_line(0, y, w, y, fill=pc("cyan"), dash=(4, 3))

    # --- Spikes -------------------------------------------------------------
    def _update_spike_count(self):
        self.stat_labels["total_spikes"].configure(text=str(len(self.spikes)))
        if self.spike_widgets:
            if self._empty_shown:
                self.empty_label.pack_forget()
                self._empty_shown = False
        else:
            if not self._empty_shown:
                self.empty_label.pack(pady=44)
                self._empty_shown = True

    def _reset_spikes(self):
        for w in list(self.spike_widgets.values()):
            try:
                w["card"].destroy()
            except Exception:
                pass
        self.spikes.clear()
        self.spike_widgets.clear()
        self._deleted.clear()
        self._update_spike_count()

    def _handle_spike(self, payload):
        spike_id = payload["id"]
        if spike_id in self._deleted:
            return
        is_new = spike_id not in self.spikes
        self.spikes[spike_id] = payload

        if is_new:
            self._create_card(payload, top=True)
            self._update_spike_count()
            play_spike_sound()
            if self.app.active_tab_id != self.tab_id:
                self.app.flash_tab(self.tab_id)
        else:
            self._update_card(payload)

    def _handle_vod_done(self, payload):
        self._reset_spikes()
        for s in payload["spikes"]:
            self.spikes[s["id"]] = s
            self._create_card(s, top=False)
        self._update_spike_count()
        self.stat_labels["total_messages"].configure(text=f"{payload['total']:,}")
        self.set_status(f"{len(payload['spikes'])} spikes from {payload['total']:,} msgs", "ok")

    @staticmethod
    def _style_of(spike: dict) -> dict:
        if spike.get("manual"):
            return SPIKE_STYLES["manual"]
        if spike.get("from_vod"):
            return SPIKE_STYLES["vod"]
        return SPIKE_STYLES["live"] if spike["status"] == "active" else SPIKE_STYLES["ended"]

    @staticmethod
    def _format_title(spike: dict) -> str:
        start = spike.get("start_vod", "??:??:??")
        end = spike.get("end_vod", "??:??:??")
        if spike.get("manual"):
            return f"Manual Mark  ·  {start}"
        if spike["status"] == "active":
            return f"{start}  —  LIVE"
        return f"{start}  —  {end}"

    @staticmethod
    def _stats_text(spike: dict) -> str:
        return f"Peak {spike['peak']} msgs/10s  ·  avg {spike['average']}  ·  {spike['intensity']}x"

    def _create_card(self, spike: dict, top: bool = True):
        st = self._style_of(spike)
        is_manual = bool(spike.get("manual"))
        is_active = spike["status"] == "active"

        card = ctk.CTkFrame(self.spike_feed, fg_color=st["bg"], corner_radius=12, width=10, height=10)
        existing = [w["card"] for w in self.spike_widgets.values()]
        if top and existing:
            card.pack(fill="x", padx=4, pady=5, before=existing[-1])
        else:
            card.pack(fill="x", padx=4, pady=5)

        bar = ctk.CTkFrame(card, width=5, height=10, corner_radius=3, fg_color=st["accent"])
        bar.pack(side="left", fill="y", padx=(10, 0), pady=12)

        body = ctk.CTkFrame(card, fg_color="transparent", width=10, height=10)
        body.pack(side="left", fill="both", expand=True, padx=(10, 8), pady=8)

        head = ctk.CTkFrame(body, fg_color="transparent", height=26)
        head.pack(fill="x")
        title = ctk.CTkLabel(
            head, text=self._format_title(spike),
            image=material_icon(st["icon"], 18, st["accent"]), compound="left",
            font=F(13, True), text_color=st["accent"], anchor="w",
        )
        title.pack(side="left")

        ctk.CTkButton(
            head, text="", image=material_icon("close", 16, P["sub"]),
            width=24, height=24, corner_radius=8, fg_color="transparent",
            hover_color=P["red"],
            command=lambda sid=spike["id"]: self._delete_spike(sid),
        ).pack(side="right")
        copy_btn = ctk.CTkButton(
            head, text="Copy time", width=70, height=24, corner_radius=8, font=F(11),
            fg_color=P["surface3"], hover_color=P["gray_btn_hover"], text_color=P["text"],
        )
        copy_btn.configure(command=lambda sid=spike["id"], b=copy_btn: self._copy_time(sid, b))
        copy_btn.pack(side="right", padx=(0, 6))

        dur = stats = prog = None
        if not is_manual:
            dur = ctk.CTkLabel(
                head, text="LIVE" if is_active else fmt_dur(spike.get("duration", 0)),
                image=material_icon("circle" if is_active else "timer", 11,
                                    st["accent"] if is_active else P["sub"]),
                compound="left",
                font=F(11, True), text_color=st["accent"] if is_active else P["sub"],
            )
            dur.pack(side="right", padx=(0, 10))

            stats = ctk.CTkLabel(body, text=self._stats_text(spike), font=F(11), text_color=P["amber"], anchor="w")
            stats.pack(fill="x", pady=(2, 4))
            prog = ctk.CTkProgressBar(body, height=6, corner_radius=3,
                                      progress_color=st["accent"], fg_color=P["surface3"])
            prog.set(min(1.0, float(spike.get("intensity", 0)) / 6.0))
            prog.pack(fill="x")

        samples = spike.get("samples", [])
        if samples:
            sf = ctk.CTkFrame(body, fg_color="transparent", width=10, height=10)
            sf.pack(fill="x", pady=(6, 0))
            for m in samples[:5]:
                text = f"{m.get('user', '?')}: {m.get('message', '')}"
                if len(text) > 110:
                    text = text[:109] + "…"
                ctk.CTkLabel(sf, text=text, font=F(11), text_color=P["sub"], anchor="w",
                             justify="left", wraplength=380).pack(fill="x")

        self.spike_widgets[spike["id"]] = {
            "card": card, "bar": bar, "title": title, "dur": dur, "stats": stats, "prog": prog,
        }

    def _update_card(self, spike: dict):
        w = self.spike_widgets.get(spike["id"])
        if not w:
            return
        st = self._style_of(spike)
        is_active = spike["status"] == "active"
        w["card"].configure(fg_color=st["bg"])
        w["bar"].configure(fg_color=st["accent"])
        w["title"].configure(
            text=self._format_title(spike),
            image=material_icon(st["icon"], 18, st["accent"]),
            text_color=st["accent"],
        )
        if w["dur"] is not None:
            w["dur"].configure(
                text="LIVE" if is_active else fmt_dur(spike.get("duration", 0)),
                image=material_icon("circle" if is_active else "timer", 11,
                                    st["accent"] if is_active else P["sub"]),
                text_color=st["accent"] if is_active else P["sub"],
            )
        if w["stats"] is not None:
            w["stats"].configure(text=self._stats_text(spike))
        if w["prog"] is not None:
            w["prog"].configure(progress_color=st["accent"])
            w["prog"].set(min(1.0, float(spike.get("intensity", 0)) / 6.0))

    def _delete_spike(self, sid):
        spike = self.spikes.get(sid)
        if not spike:
            return
        ts = spike.get("start_vod", "")
        msg = f"Delete manual mark at {ts}?" if spike.get("manual") else f"Delete spike at {ts}?"
        self._confirm_delete(msg, lambda: self._do_delete_spike(sid))

    def _do_delete_spike(self, sid):
        w = self.spike_widgets.pop(sid, None)
        if w:
            try:
                w["card"].destroy()
            except Exception:
                pass
        self.spikes.pop(sid, None)
        self._deleted.add(sid)
        self._update_spike_count()

    def _confirm_delete(self, message: str, on_yes):
        ThemedConfirmationDialog(
            self.app, "Confirm delete", message, "Delete", "Cancel", on_yes,
        )

    def _copy_time(self, sid, btn):
        spike = self.spikes.get(sid)
        if not spike:
            return
        ts = spike.get("start_vod", "")
        self.app.clipboard_clear()
        self.app.clipboard_append(ts)
        self.set_status(f"Copied {ts}", "ok")
        try:
            btn.configure(text="Copied")
            btn.after(1500, lambda: btn.configure(text="Copy time"))
        except Exception:
            pass

    # --- Exports ------------------------------------------------------------
    def _channel_for_files(self) -> str:
        return self.picker.channel or "session"

    def _export_spikes(self):
        if not self.spikes:
            self.set_status("No spikes to export", "warn")
            return
        stamp = today_stamp()
        path = filedialog.asksaveasfilename(
            parent=self.app, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"{stamp}_{self._channel_for_files()}_spikes.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Start_Time", "End_Time", "Duration_s", "Peak", "Average", "Samples"])
                for s in self.spikes.values():
                    samples = "; ".join(
                        f"{m.get('user', '?')}: {m.get('message', '')}"
                        for m in s.get("samples", [])
                    )
                    w.writerow([
                        s.get("start_vod", ""), s.get("end_vod", ""),
                        s.get("duration", 0), s.get("peak", 0),
                        s.get("average", 0), samples,
                    ])
            self.set_status(f"Saved: {os.path.basename(path)}", "ok")
        except Exception as e:
            self.set_status(f"Export failed: {e}", "err")

    def _export_chat(self):
        # Prefer the live worker's current file
        if self.worker and os.path.exists(self.worker.chat_log_path):
            src = self.worker.chat_log_path
        else:
            src = os.path.join(
                get_log_dir(),
                f"{today_stamp()}_{self._channel_for_files()}_chat.csv",
            )
        if not os.path.exists(src):
            self.set_status("No chat log available", "warn")
            return
        path = filedialog.asksaveasfilename(
            parent=self.app, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"{today_stamp()}_{self._channel_for_files()}_chat.csv",
        )
        if not path:
            return
        try:
            shutil.copy2(src, path)
            self.set_status(f"Saved: {os.path.basename(path)}", "ok")
        except Exception as e:
            self.set_status(f"Export failed: {e}", "err")

    def _open_logs(self):
        folder = get_log_dir()
        os.makedirs(folder, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            self.set_status(f"Logs: {folder}", "info")


class VODURLDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app, fg_color=P["bg"])
        self.app = app
        self.url = None
        self.title("Import VOD chat")
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        app.apply_window_icon(self)

        ctk.CTkLabel(
            self, text="Import chat from a VOD",
            font=F(18, True), text_color=P["text"],
        ).pack(anchor="w", padx=22, pady=(20, 4))
        ctk.CTkLabel(
            self, text="Paste a Twitch VOD or clip URL. YouTube and Kick links also work.",
            font=F(12), text_color=P["sub"], wraplength=430, justify="left",
        ).pack(anchor="w", padx=22, pady=(0, 14))
        self.entry = ctk.CTkEntry(
            self, height=38, corner_radius=9, font=F(12),
            fg_color=P["surface"], border_color=P["stroke"], text_color=P["text"],
            placeholder_text="https://...",
        )
        self.entry.pack(fill="x", padx=22, pady=(0, 18))
        self.entry.bind("<Return>", self._submit)
        self.bind("<Escape>", lambda _event: self._cancel())

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 18))
        ctk.CTkButton(
            row, text="Cancel", width=100, height=34, corner_radius=8, font=F(12, True),
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            command=self._cancel,
        ).pack(side="right")
        ctk.CTkButton(
            row, text="Import", image=material_icon("cloud-download", 16, "#FFFFFF"),
            compound="left", width=110, height=34, corner_radius=8, font=F(12, True),
            fg_color=P["accent"], hover_color=P["accent_hover"], text_color="#FFFFFF",
            command=self._submit,
        ).pack(side="right", padx=(0, 8))
        app.center_dialog(self, 480, 200)
        self.grab_set()
        self.after(100, self.entry.focus_set)

    def get_input(self):
        self.app.wait_window(self)
        return self.url

    def _submit(self, _event=None):
        self.url = self.entry.get().strip()
        self.destroy()

    def _cancel(self):
        self.url = None
        self.destroy()


class VODProgressDialog(ctk.CTkToplevel):
    def __init__(self, app, on_cancel):
        super().__init__(app, fg_color=P["bg"])
        self.title("VOD chat import")
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", on_cancel)
        app.apply_window_icon(self)

        self.title_label = ctk.CTkLabel(
            self, text="Importing VOD chat",
            font=F(17, True), text_color=P["text"],
        )
        self.title_label.pack(anchor="w", padx=22, pady=(20, 8))
        self.progress_label = ctk.CTkLabel(
            self, text="Downloading... 1%", font=F(12, True), text_color=P["sub"],
        )
        self.progress_label.pack(anchor="w", padx=22, pady=(0, 10))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 20))
        self.progress_bar = ctk.CTkProgressBar(
            row, height=14, corner_radius=7, progress_color=P["accent"],
            fg_color=P["surface3"],
        )
        self.progress_bar.set(0.01)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.cancel_button = ctk.CTkButton(
            row, text="Cancel download", width=132, height=34, corner_radius=8,
            font=F(11, True), fg_color=P["red"], hover_color=P["red_hover"],
            text_color="#FFFFFF", command=on_cancel,
        )
        self.cancel_button.pack(side="right")
        app.center_dialog(self, 490, 158)

    def set_progress(self, percent: int, phase: str):
        percent = min(100, max(1, int(percent)))
        self.progress_label.configure(text=f"{phase}... {percent}%")
        self.progress_bar.set(percent / 100)

    def on_theme_changed(self):
        self.configure(fg_color=P["bg"])
        self.title_label.configure(text_color=P["text"])
        self.progress_label.configure(text_color=P["sub"])
        self.progress_bar.configure(
            progress_color=P["accent"], fg_color=P["surface3"],
        )
        self.cancel_button.configure(
            fg_color=P["red"], hover_color=P["red_hover"],
        )


class ThemedConfirmationDialog(ctk.CTkToplevel):
    def __init__(
        self,
        app,
        title: str,
        message: str,
        confirm_text: str,
        cancel_text: str,
        on_confirm,
    ):
        super().__init__(app, fg_color=P["bg"])
        self.title(title)
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._confirm(on_confirm))
        app.apply_window_icon(self)

        ctk.CTkLabel(
            self, text=title, font=F(16, True), text_color=P["text"],
        ).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(
            self, text=message, font=F(12), text_color=P["sub"],
            wraplength=350, justify="left",
        ).pack(anchor="w", padx=22, pady=(0, 16))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 18))
        ctk.CTkButton(
            row, text=cancel_text, width=145, height=34, corner_radius=8, font=F(11, True),
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            command=self.destroy,
        ).pack(side="right")
        ctk.CTkButton(
            row, text=confirm_text, width=145, height=34, corner_radius=8, font=F(11, True),
            fg_color=P["red"], hover_color=P["red_hover"], text_color="#FFFFFF",
            command=lambda: self._confirm(on_confirm),
        ).pack(side="right", padx=(0, 8))
        app.center_dialog(self, 400, 174)
        self.grab_set()

    def _confirm(self, callback):
        self.destroy()
        callback()


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------
class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app, fg_color=P["bg"])
        self.app = app
        self.title("Settings")
        w, h = 480, 650
        x = app.winfo_rootx() + max(0, (app.winfo_width() - w) // 2)
        y = app.winfo_rooty() + 40
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)
        app.apply_window_icon(self)

        self.content = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=P["surface3"],
            scrollbar_button_hover_color=P["gray_btn_hover"],
        )
        self.content.pack(fill="both", expand=True)
        ctk.CTkLabel(self.content, text="Settings", font=F(22, True), text_color=P["text"]).pack(anchor="w", padx=24, pady=(20, 2))
        ctk.CTkLabel(self.content, text="Changes are applied instantly and saved automatically.",
                     font=F(12), text_color=P["sub"]).pack(anchor="w", padx=24, pady=(0, 10))

        # --- Appearance ---
        card = self._section("Appearance")
        row = self._row(card)
        ctk.CTkLabel(row, text="Theme", font=F(13), text_color=P["text"]).pack(side="left")
        self.theme_btn = ctk.CTkSegmentedButton(
            row, values=["Dark", "Light"], command=self._on_theme, font=F(12, True),
            selected_color=P["accent"], selected_hover_color=P["accent_hover"],
            unselected_color=P["surface3"], unselected_hover_color=P["gray_btn_hover"],
            fg_color=P["surface3"], text_color=("#FFFFFF", "#FFFFFF"), width=180,
        )
        self.theme_btn.pack(side="right")
        self.theme_btn.set("Dark" if SETTINGS["theme"] == "dark" else "Light")
        row = self._row(card)
        ctk.CTkLabel(row, text="Animated particles", font=F(13), text_color=P["text"]).pack(side="left")
        self.particles_sw = ctk.CTkSwitch(row, text="", width=46, progress_color=P["accent"], command=self._on_particles)
        self.particles_sw.pack(side="right")
        if SETTINGS["particles"]:
            self.particles_sw.select()

        row = self._row(card)
        ctk.CTkLabel(row, text="Launch in fullscreen", font=F(13), text_color=P["text"]).pack(side="left")
        self.fs_sw = ctk.CTkSwitch(row, text="", width=46, progress_color=P["accent"], command=self._on_fullscreen)
        self.fs_sw.pack(side="right")
        if SETTINGS.get("fullscreen"):
            self.fs_sw.select()
        ctk.CTkLabel(card, text="Tip: Alt+Enter, Ctrl+Enter, or F11 toggles fullscreen.",
                     font=F(10), text_color=P["sub"]).pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack()

        # --- Sound ---
        card = self._section("Spike sound")
        row = self._row(card)
        ctk.CTkLabel(row, text="Play sound on spike", font=F(13), text_color=P["text"]).pack(side="left")
        self.sound_sw = ctk.CTkSwitch(row, text="", width=46, progress_color=P["accent"], command=self._on_sound)
        self.sound_sw.pack(side="right")
        if SETTINGS["sound"]:
            self.sound_sw.select()
        row = self._row(card)
        ctk.CTkLabel(row, text="Volume", font=F(13), text_color=P["text"]).pack(side="left")
        self.vol_label = ctk.CTkLabel(row, text=f"{int(SETTINGS['volume'] * 100)}%", font=F(12, True),
                                      text_color=P["sub"], width=40)
        self.vol_label.pack(side="right")
        self.vol_slider = ctk.CTkSlider(row, from_=0, to=100, number_of_steps=100, width=170,
                                        progress_color=P["accent"], button_color=P["accent"],
                                        button_hover_color=P["accent_hover"], command=self._on_volume)
        self.vol_slider.set(SETTINGS["volume"] * 100)
        self.vol_slider.pack(side="right", padx=8)
        row = self._row(card)
        ctk.CTkButton(
            row, text="Test sound", image=material_icon("volume-high", 16, P["text"]),
            compound="left", width=120, height=30, corner_radius=8, font=F(12, True),
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            command=self._test_sound,
        ).pack(side="left")
        hint = "spike_notif.mp3 found" if os.path.exists(SPIKE_SOUND_PATH) else "spike_notif.mp3 not found"
        if not PYGAME_OK:
            hint = "pygame not installed"
        self.sound_hint = ctk.CTkLabel(row, text=hint, font=F(11), text_color=P["sub"])
        self.sound_hint.pack(side="left", padx=10)
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack()

        # --- Defaults ---
        card = self._section("Defaults")
        row = self._row(card)
        ctk.CTkLabel(row, text="Sensitivity for new tabs", font=F(13), text_color=P["text"]).pack(side="left")
        self.sens_var = ctk.StringVar(value=SETTINGS["default_sensitivity"])
        ctk.CTkOptionMenu(
            row, values=list(SENSITIVITY_OPTIONS.keys()), variable=self.sens_var, width=190, height=30,
            font=F(12), dropdown_font=F(12), corner_radius=8, fg_color=P["surface3"],
            button_color=P["gray_btn_hover"], button_hover_color=P["accent"], text_color=P["text"],
            dropdown_fg_color=P["surface"], dropdown_hover_color=P["surface3"], dropdown_text_color=P["text"],
            command=self._on_sens,
        ).pack(side="right")
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack()

        # --- Chat ---
        card = self._section("Live chat")
        row = self._row(card)
        ctk.CTkLabel(row, text="Font size", font=F(13), text_color=P["text"]).pack(side="left")
        self.font_label = ctk.CTkLabel(row, text=f"{SETTINGS['chat_font']} pt", font=F(12, True),
                                       text_color=P["sub"], width=48)
        self.font_label.pack(side="right")
        self.font_slider = ctk.CTkSlider(row, from_=10, to=20, number_of_steps=10, width=170,
                                         progress_color=P["accent"], button_color=P["accent"],
                                         button_hover_color=P["accent_hover"], command=self._on_font)
        self.font_slider.set(SETTINGS["chat_font"])
        self.font_slider.pack(side="right", padx=8)
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack()

        # --- Logs folder ---
        card = self._section("Log files")
        row = self._row(card)
        ctk.CTkLabel(row, text="Save logs to", font=F(13), text_color=P["text"]).pack(side="left")
        self.logs_path_lbl = ctk.CTkLabel(
            row, text=self._logs_folder_short(), font=F(11), text_color=P["sub"],
            wraplength=160, justify="right",
        )
        self.logs_path_lbl.pack(side="right", padx=(4, 8))
        ctk.CTkButton(
            row, text="Browse…", width=90, height=28, corner_radius=8, font=F(11, True),
            fg_color=P["gray_btn"], hover_color=P["gray_btn_hover"], text_color=P["text"],
            command=self._pick_logs_folder,
        ).pack(side="right")
        ctk.CTkButton(
            card, text="Reset to default folder", height=26, corner_radius=8, font=F(11),
            fg_color="transparent", hover_color=P["surface3"], text_color=P["sub"],
            command=self._reset_logs_folder,
        ).pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkFrame(card, height=4, fg_color="transparent").pack()

        ctk.CTkButton(self, text="Done", height=38, corner_radius=10, font=F(13, True),
                      fg_color=P["accent"], hover_color=P["accent_hover"], text_color="#FFFFFF",
                      command=self._close).pack(fill="x", padx=24, pady=(14, 20), side="bottom")

        self.after(150, self._focus)

    def _focus(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _section(self, title: str):
        card = ctk.CTkFrame(self.content, fg_color=P["surface"], corner_radius=14, border_width=1, border_color=P["stroke"])
        card.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(card, text=title.upper(), font=F(10, True), text_color=P["sub"]).pack(anchor="w", padx=16, pady=(12, 2))
        return card

    @staticmethod
    def _row(parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=5)
        return row

    def _close(self):
        self.app.settings_win = None
        self.destroy()

    # --- handlers ---
    def _on_theme(self, value):
        self.app.set_theme("dark" if value == "Dark" else "light")

    def _on_particles(self):
        on = bool(self.particles_sw.get())
        SETTINGS["particles"] = on
        self.app.particles.set_enabled(on)
        self.app.save_state()

    def _on_fullscreen(self):
        on = bool(self.fs_sw.get())
        SETTINGS["fullscreen"] = on
        try:
            self.app.attributes("-fullscreen", on)
        except Exception:
            pass
        self.app.save_state()

    def _on_sound(self):
        SETTINGS["sound"] = bool(self.sound_sw.get())
        self.app.save_state()

    def _on_volume(self, v):
        SETTINGS["volume"] = round(float(v) / 100, 2)
        self.vol_label.configure(text=f"{int(float(v))}%")
        self.app.save_state()

    def _test_sound(self):
        if not PYGAME_OK or not _audio_ok:
            self.sound_hint.configure(text="Audio unavailable — install pygame-ce")
            return
        if not os.path.exists(SPIKE_SOUND_PATH):
            self.sound_hint.configure(text="Add spike_notif.mp3 next to the script")
            return
        play_spike_sound(force=True)

    def _on_sens(self, value):
        SETTINGS["default_sensitivity"] = value
        self.app.save_state()

    def _on_font(self, v):
        size = int(round(float(v)))
        SETTINGS["chat_font"] = size
        self.font_label.configure(text=f"{size} pt")
        for tab in self.app.tabs.values():
            tab.apply_chat_font()
        self.app.save_state()

    def _logs_folder_short(self) -> str:
        folder = get_log_dir()
        if len(folder) > 32:
            return "…" + folder[-31:]
        return folder

    def _pick_logs_folder(self):
        folder = filedialog.askdirectory(
            title="Choose logs folder",
            initialdir=get_log_dir(),
            parent=self,
        )
        if not folder:
            return
        SETTINGS["logs_folder"] = folder
        self.app.save_state()
        self.logs_path_lbl.configure(text=self._logs_folder_short())

    def _reset_logs_folder(self):
        SETTINGS["logs_folder"] = ""
        self.app.save_state()
        self.logs_path_lbl.configure(text=self._logs_folder_short())


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class SpikeTrackerApp(ctk.CTk):
    M = 18
    HEADER_H = 32
    TABS_X = 18
    CHIP_MIN, CHIP_MAX = 76, 180

    def __init__(self):
        super().__init__()
        self.title("Twitch PeakChat by devenci")
        scale = self.scale()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1500, max(640, int((screen_width - 32) / scale)))
        height = min(900, max(480, int((screen_height - 48) / scale)))
        x = max(0, (screen_width - round(width * scale)) // 2)
        y = max(0, (screen_height - round(height * scale)) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(1100, width), min(720, height))
        self.configure(fg_color=P["bg"])

        self.saved_streamers = load_streamers()
        self.tabs: dict = {}
        self.tab_order: list = []
        self.chips: dict = {}
        self.active_tab_id: str | None = None
        self.open_picker: StreamerPicker | None = None
        self.settings_win = None
        self._layout_job = None
        self._last_size = (0, 0)
        self._restoring = False
        self._logo_ctk_cache: dict = {}

        self.avatars = AvatarManager()

        # Logo / window icon
        self.logo_pil = load_logo()
        self.window_icon_path = find_asset(WINDOW_ICON_NAME)
        window_icon_pil = self.logo_pil
        if self.window_icon_path:
            try:
                with Image.open(self.window_icon_path) as icon_file:
                    window_icon_pil = icon_file.convert("RGBA")
            except OSError as e:
                print(f"[Icon] Could not load {self.window_icon_path}: {e}")
        self.icon_photo = None
        try:
            self.icon_photo = ImageTk.PhotoImage(window_icon_pil.resize((64, 64), RESAMPLE))
            self.iconphoto(True, self.icon_photo)
            self.after(300, self._reapply_icon)
        except Exception as e:
            print(f"[Icon] {e}")
        if self.window_icon_path and sys.platform == "win32":
            try:
                self.iconbitmap(default=self.window_icon_path)
            except tk.TclError as e:
                print(f"[Icon] Could not set {self.window_icon_path}: {e}")

        # Background canvas
        self.bg_canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=pc("bg"))
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self.bg_canvas)

        self.particles = ParticleBackground(self, self.bg_canvas, count=34)
        self.particles.enabled = bool(SETTINGS["particles"])

        # Header buttons (slightly smaller)
        self.settings_btn = ctk.CTkButton(
            self, text="", image=material_icon("cog", 20, P["text"]),
            width=36, height=36, corner_radius=10, font=F(16),
            fg_color=P["surface"], hover_color=P["surface3"], text_color=P["text"],
            border_width=1, border_color=P["stroke"], command=self.open_settings,
        )
        self.newtab_btn = ctk.CTkButton(
            self, text="New Tab", image=material_icon("plus", 16, "#FFFFFF"),
            width=104, height=36, corner_radius=10, font=F(12, True),
            fg_color=P["accent"], hover_color=P["accent_hover"], text_color="#FFFFFF",
            command=lambda: self.new_tab(),
        )

        # Empty state card
        self.empty_card = make_card(self, radius=18)
        self.empty_card.configure(width=420, height=150)
        ctk.CTkLabel(self.empty_card, text="No tabs open", font=F(20, True), text_color=P["text"]).pack(pady=(34, 4))
        ctk.CTkLabel(self.empty_card, text="Click  + New Tab  to start tracking a streamer.",
                     font=F(13), text_color=P["sub"]).pack()

        # Bindings
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._global_click, add="+")
        self.bind("<Control-t>", lambda e: self.new_tab())
        self.bind("<Control-w>", lambda e: self.close_tab(self.active_tab_id) if self.active_tab_id else None)
        self.bind("<Alt-Return>", lambda e: self.toggle_fullscreen())
        self.bind("<Control-Return>", lambda e: self.toggle_fullscreen())
        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Restore tabs
        self._restoring = True
        saved_tabs = [t for t in SETTINGS.get("tabs", []) if isinstance(t, dict)]
        for t in saved_tabs:
            ch = str(t.get("channel", "")).lower().strip()
            sens = t.get("sens")
            if sens not in SENSITIVITY_OPTIONS:
                sens = SETTINGS["default_sensitivity"]
            self.new_tab(ch if TWITCH_NAME_RE.match(ch) else "", sens, activate=False)
        if not self.tab_order:
            self.new_tab(activate=False)
        idx = SETTINGS.get("active", 0)
        idx = idx if isinstance(idx, int) and 0 <= idx < len(self.tab_order) else 0
        self._restoring = False
        self.activate_tab(self.tab_order[idx])
        self.save_state()

        # Restore fullscreen
        if SETTINGS.get("fullscreen"):
            self.after(200, lambda: self.attributes("-fullscreen", True))

        self.after(250, self.particles.populate)
        self.after(300, self.particles.start)
        self.after(100, self._pump)

    # --- utils ---
    def scale(self) -> float:
        try:
            return float(self._get_window_scaling())
        except Exception:
            return 1.0

    def _reapply_icon(self):
        self._set_window_icon(self)

    def apply_window_icon(self, window):
        self._set_window_icon(window)
        if window is not self:
            self.after(300, lambda: self._reapply_dialog_icon(window))

    def _reapply_dialog_icon(self, window):
        try:
            if window.winfo_exists():
                self._set_window_icon(window)
        except tk.TclError:
            return

    def _set_window_icon(self, window):
        if self.window_icon_path and sys.platform == "win32":
            try:
                window.iconbitmap(default=self.window_icon_path)
            except tk.TclError as e:
                print(f"[Icon] Could not set {self.window_icon_path}: {e}")
        if self.icon_photo is not None:
            try:
                window.iconphoto(window is self, self.icon_photo)
            except tk.TclError as e:
                print(f"[Icon] Could not set window icon: {e}")

    def center_dialog(self, dialog, width: int, height: int):
        self.update_idletasks()
        scale = self.scale()
        width_px = round(width * scale)
        height_px = round(height * scale)
        x = self.winfo_rootx() + (self.winfo_width() - width_px) // 2
        y = self.winfo_rooty() + (self.winfo_height() - height_px) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")

    def logo_image(self, size: int) -> ctk.CTkImage:
        if size not in self._logo_ctk_cache:
            self._logo_ctk_cache[size] = ctk.CTkImage(
                light_image=self.logo_pil, dark_image=self.logo_pil, size=(size, size))
        return self._logo_ctk_cache[size]

    def _pump(self):
        try:
            self.avatars.pump()
            self.after(100, self._pump)
        except tk.TclError:
            pass

    def _global_click(self, event):
        if self.open_picker is not None:
            self.open_picker.on_global_click(event)
        # Close tab-level import menu on outside click
        if self.active_tab_id and self.active_tab_id in self.tabs:
            tab = self.tabs[self.active_tab_id]
            if tab._import_menu is not None:
                w = str(event.widget)
                menu_path = str(tab._import_menu)
                button_path = str(tab.import_btn)
                inside_menu = w == menu_path or w.startswith(menu_path + ".")
                inside_button = w == button_path or w.startswith(button_path + ".")
                if not inside_menu and not inside_button:
                    tab._close_import_menu()

    # --- layout ---
    def _on_configure(self, event):
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        if self._layout_job is None:
            self._layout_job = self.after(16, self._layout)

    def schedule_layout(self):
        if self._layout_job is None:
            self._layout_job = self.after(1, self._layout)

    def _layout(self):
        self._layout_job = None
        s = self.scale()
        W = int(self.winfo_width() / s)
        H = int(self.winfo_height() / s)
        if W < 300 or H < 300:
            return
        M = self.M

        # Header buttons (right side, smaller)
        right = W - M
        self.settings_btn.place(x=right - 36, y=M + 2)
        right -= 36 + 8
        self.newtab_btn.place(x=right - 104, y=M + 2)
        right -= 104 + 12

        # Tab chips
        n = len(self.tab_order)
        area = max(100, right - self.TABS_X)
        if n:
            w = (area - (n - 1) * 6) / n
            w = max(self.CHIP_MIN, min(self.CHIP_MAX, w))
            x = self.TABS_X
            for tid in self.tab_order:
                chip = self.chips[tid]
                chip.set_width(w)
                put(chip, x, M + 2, w, 40)
                x += w + 6

        top = M + self.HEADER_H + 16
        if self.active_tab_id and self.active_tab_id in self.tabs:
            self.empty_card.place_forget()
            self.tabs[self.active_tab_id].layout(W, H, top)
        else:
            self.empty_card.place(relx=0.5, rely=0.5, anchor="center")

    # --- tabs ---
    def new_tab(self, channel: str = "", sens: str | None = None, activate: bool = True):
        tab_id = uuid.uuid4().hex[:8]
        sens = sens if sens in SENSITIVITY_OPTIONS else SETTINGS["default_sensitivity"]
        chip = TabChip(self, tab_id)
        tab = StreamTab(self, tab_id, sens)
        self.chips[tab_id] = chip
        self.tabs[tab_id] = tab
        self.tab_order.append(tab_id)
        tab.hide()
        if channel:
            tab.picker.commit(channel, silent=True)
        if activate:
            self.activate_tab(tab_id)
        else:
            self.schedule_layout()
        self.save_state()
        return tab_id

    def activate_tab(self, tab_id: str):
        if tab_id not in self.tabs:
            return
        if self.open_picker is not None:
            self.open_picker.close_popup()
        self.active_tab_id = tab_id
        for tid, tab in self.tabs.items():
            if tid == tab_id:
                tab.show()
            else:
                tab.hide()
            self.chips[tid].set_active(tid == tab_id)
        self._layout()
        self.save_state()

    def close_tab(self, tab_id: str | None):
        if not tab_id or tab_id not in self.tabs:
            return
        idx = self.tab_order.index(tab_id)
        self.tabs[tab_id].destroy()
        try:
            self.chips[tab_id].destroy()
        except Exception:
            pass
        del self.tabs[tab_id]
        del self.chips[tab_id]
        self.tab_order.pop(idx)

        if not self.tab_order:
            self.active_tab_id = None
            self.schedule_layout()
        else:
            new_idx = max(0, min(idx, len(self.tab_order) - 1))
            self.activate_tab(self.tab_order[new_idx])
        self.save_state()

    def rename_tab(self, tab_id: str, channel: str):
        if tab_id in self.chips:
            self.chips[tab_id].set_channel(channel)

    def set_chip_state(self, tab_id: str, kind: str):
        if tab_id in self.chips:
            self.chips[tab_id].set_state_dot(kind)

    def flash_tab(self, tab_id: str):
        if tab_id in self.chips:
            self.chips[tab_id].flash()

    def refresh_saved_state(self):
        for tab in self.tabs.values():
            tab._refresh_save_btn()

    def save_state(self):
        if self._restoring:
            return
        tabs_data = []
        for tid in self.tab_order:
            tab = self.tabs[tid]
            tabs_data.append({
                "channel": tab.picker.channel or "",
                "sens": tab.sensitivity_var.get(),
            })
        SETTINGS["tabs"] = tabs_data
        active_idx = 0
        if self.active_tab_id in self.tab_order:
            active_idx = self.tab_order.index(self.active_tab_id)
        SETTINGS["active"] = active_idx
        save_settings()

    # --- settings / theme ---
    def open_settings(self):
        if self.settings_win is not None:
            try:
                if not self.settings_win.winfo_exists():
                    raise tk.TclError("Settings window no longer exists")
                self.settings_win.deiconify()
                self.settings_win.lift()
                self.settings_win.focus_force()
                return
            except tk.TclError:
                self.settings_win = None
        self.settings_win = SettingsDialog(self)

    def set_theme(self, mode: str):
        settings_win = self.settings_win
        SETTINGS["theme"] = mode
        ctk.set_appearance_mode(mode)
        self.configure(fg_color=P["bg"])
        try:
            self.bg_canvas.configure(bg=pc("bg"))
        except Exception:
            pass
        self.particles.recolor()
        for tab in self.tabs.values():
            tab.on_theme_changed()
        self.save_state()
        if settings_win is not None:
            self.after(
                30,
                lambda: self.open_settings()
                if self.settings_win is settings_win else None,
            )

    def toggle_fullscreen(self):
        current = bool(self.attributes("-fullscreen"))
        self.attributes("-fullscreen", not current)
        SETTINGS["fullscreen"] = not current
        self.save_state()

    def _on_close(self):
        try:
            self.save_state()
        except Exception:
            pass
        for tab in list(self.tabs.values()):
            try:
                tab.destroy()
            except Exception:
                pass
        try:
            self.particles.clear()
        except Exception:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    SETTINGS = load_settings()
    ctk.set_appearance_mode(SETTINGS["theme"])
    ctk.set_default_color_theme("dark-blue")

    init_audio()

    os.makedirs(LOG_DIR, exist_ok=True)
    if SETTINGS.get("logs_folder"):
        try:
            os.makedirs(SETTINGS["logs_folder"], exist_ok=True)
        except Exception:
            SETTINGS["logs_folder"] = ""

    app = SpikeTrackerApp()
    app.mainloop()