#!/usr/bin/env python3
"""
Dynamic Island for KDE / Linux  (PySide6)

Features
  - Spring-physics animation (the pill stretches and bounces like iOS)
  - Music player (MPRIS via playerctl): album art, progress, prev/play/next, seek
  - Notifications (listens to the D-Bus session bus)
  - Volume overlay (pactl subscribe, works with PulseAudio and PipeWire)
  - Battery: plugged / unplugged / low battery
  - Timer with a progress ring in the collapsed pill
  - Expanded view has 4 pages (mouse wheel or click the dots):
        Home (music / clock) | System (CPU, RAM, network, temp) |
        Focus (Pomodoro + CTF countdown + reminders) | Launcher (app buttons)
  - Overlays: Caps/Num Lock, screen brightness, VPN (tun0/wg0...) and Wi-Fi alerts
  - CTF events and reminders with alerts (1 day / 1 hour / 10 min / start)
  - Control from the terminal:
        island timer 5m | toast "hi" | toggle | page system | pomodoro [stop|skip]
        island ctf "HTB Event" "2026-11-20 19:00" | remind "Class" "08:00" daily
        island forget "HTB Event" | reload | cancel | quit
  - Shelf page: drop up to 5 files/folders on the island, keep them temporarily (memory only,
    gone when the island quits), drag them out again, or "Copy all" and Ctrl+V in any file manager
        island shelf add FILE... | shelf paste | shelf copy | shelf clear
  - Themes: black, glass, clear, light, midnight, hacker, sunset, album (+ your own)
        island theme glass | theme next | style opacity 0.5 | style bg "#102030" | style reset
  - Config file: ~/.config/dynamic-island/config.json (events, launchers, mode, theme, ...)
  - Visibility modes:
        Standby   always visible (idle pill) + popups
        Popup     hidden until something happens, then pops out and hides again
        Closed    fully hidden, nothing shows (do-not-disturb)
        Automatic Standby while music plays / a timer runs, otherwise Popup,
                  and Closed during "quiet_hours" from the config (e.g. ["23:00", "07:00"])
    Switch with: the mode button in the expanded view, right-click menu, the tray icon
    (left click = next mode, right click = menu) or:  island mode auto|standby|popup|closed|cycle

Mouse
  left click   expand / collapse   (click a notification to dismiss it)
  wheel        switch pages while expanded
  middle click play / pause
  right click  menu (timers, quit)
"""
import glob
import html
import json
import math
import os
import re
import shlex
import shutil
import signal
import sys
import time
import zlib
from collections import deque
from datetime import datetime, timedelta

# Run through XWayland so the window can place itself at top-center on KDE Wayland.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import (QFileInfo, QMimeData, QPoint, QPointF, QProcess, QProcessEnvironment,
                            QRectF, Qt, QTimer, QUrl)
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QDrag, QFont, QFontMetrics, QIcon,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRegion)
from PySide6.QtNetwork import (QLocalServer, QLocalSocket, QNetworkAccessManager,
                               QNetworkRequest)
from PySide6.QtWidgets import QApplication, QFileIconProvider, QMenu, QSystemTrayIcon, QWidget

# ----------------------------------------------------------------- settings --
SOCKET_NAME = "dynamic-island-kde"
CANVAS_W, CANVAS_H = 470, 210          # transparent window; the pill is drawn inside
TOP_MARGIN = 6                          # gap between the screen edge and the pill

# mode -> (width, height)
SIZES = {
    "expanded": (400, 180),
    "brightness": (270, 44),
    "hidden": (40, 0),
    "notif": (380, 80),
    "battery": (250, 44),
    "volume": (270, 44),
    "toast": (300, 44),
    "track": (340, 58),
}
COMPACT_IDLE = (126, 34)
COMPACT_ACTIVE = (190, 34)

WHITE = QColor(255, 255, 255)
GRAY = QColor(255, 255, 255, 140)
DIM = QColor(255, 255, 255, 60)
GREEN = QColor(48, 209, 88)
RED = QColor(255, 69, 58)
ORANGE = QColor(255, 159, 10)

PLAYER_FORMAT = "\t".join([
    "{{status}}", "{{artist}}", "{{title}}", "{{album}}",
    "{{position}}", "{{mpris:length}}", "{{mpris:artUrl}}",
])
BATTERY = next(iter(sorted(glob.glob("/sys/class/power_supply/BAT*"))), None)

CONFIG_DIR = os.path.expanduser("~/.config/dynamic-island")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
PAGES = ["home", "shelf", "system", "focus", "launch"]
MAX_SHELF = 5
DEBUG = bool(os.environ.get("ISLAND_DEBUG"))    # run with ISLAND_DEBUG=1 to print drag & drop events

THEME_BASE = {
    "bg": "#000000", "bg2": "", "opacity": 1.0,         # background (bg2 = gradient end)
    "text": "#ffffff", "accent": "", "art_accent": True,   # accent "" = same as text
    "border": "#ffffff", "border_opacity": 0.08,
    "corner": 34, "font": "", "tint": 0.0,              # tint = mix album color into bg (0..1)
}
THEMES = {
    "black": {"label": "Black"},
    "glass": {"label": "Dark glass", "bg": "#0c0c12", "opacity": 0.60, "border_opacity": 0.18},
    "clear": {"label": "Clear", "bg": "#000000", "opacity": 0.22, "border_opacity": 0.35},
    "light": {"label": "Light", "bg": "#f5f5f7", "opacity": 0.92, "text": "#1c1c1e",
              "border": "#000000", "border_opacity": 0.12},
    "midnight": {"label": "Midnight", "bg": "#0a1030", "bg2": "#1b0f3a", "opacity": 0.96,
                 "accent": "#7aa2ff", "art_accent": False, "border": "#7aa2ff", "border_opacity": 0.25},
    "hacker": {"label": "Hacker", "bg": "#010d03", "opacity": 0.94, "text": "#33ff77",
               "accent": "#33ff77", "art_accent": False, "border": "#33ff77",
               "border_opacity": 0.35, "font": "Monospace"},
    "sunset": {"label": "Sunset", "bg": "#3a1030", "bg2": "#7a2a1a", "opacity": 0.95,
               "text": "#fff1e6", "accent": "#ffb36b", "art_accent": False,
               "border": "#ffb36b", "border_opacity": 0.25},
    "album": {"label": "Album tint", "bg": "#101012", "opacity": 0.96, "tint": 0.40},
}
STYLE_NUMERIC = {"opacity", "border_opacity", "corner", "tint"}
MODES = [("auto", "Automatic"), ("standby", "Standby"), ("popup", "Popup"), ("closed", "Closed")]
MODE_LABELS = {"auto": "Auto", "standby": "Standby", "popup": "Popup", "closed": "Closed"}
MODE_COLORS = {"auto": QColor(10, 132, 255), "standby": GREEN, "popup": ORANGE, "closed": RED}
POMO = {"focus": 25 * 60, "break": 5 * 60, "long": 15 * 60}
DEFAULT_CONFIG = {
    "events": [],       # {"name", "when": "YYYY-MM-DD HH:MM", "kind": "ctf|remind", "repeat": "none|daily|weekly"}
    "launchers": [      # up to 8 buttons on the Launcher page
        {"name": "Terminal", "cmd": "konsole", "icon": "utilities-terminal"},
        {"name": "Files", "cmd": "dolphin", "icon": "system-file-manager"},
        {"name": "Browser", "cmd": "firefox-esr", "icon": "firefox-esr"},
        {"name": "Settings", "cmd": "systemsettings", "icon": "preferences-system"},
    ],
    "theme": "black",   # black | glass | clear | light | midnight | hacker | sunset | album | your own
    "themes": {},       # your own: {"mine": {"bg": "#202020", "opacity": 0.5, "text": "#ffffff"}}
    "style": {},        # tweaks on top of the theme (set with: island style KEY VALUE)
    "shelf_expire_minutes": 0,   # 0 = keep shelf items until you remove them or the island quits
    "input_mask": True,      # set to false if you still see ghost rectangles (clicks then hit the whole window)
    "x11_bypass_wm": True,   # set to false if drag & drop onto the island does not work (KDE Wayland)
    "mode": "standby",  # auto | standby | popup | closed
    "quiet_hours": [],  # e.g. ["23:00", "07:00"] -> Automatic mode switches to Closed
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, default in list(cfg.items()):
                if isinstance(data.get(key), type(default)):
                    cfg[key] = data[key]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def parse_when(s):
    """'2026-11-20 19:00' | '19:00' (next time it happens) | '+30m' (from now)."""
    s = (s or "").strip()
    if s.startswith("+"):
        d = parse_duration(s[1:])
        return datetime.now() + timedelta(seconds=d) if d else None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        t = datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return None
    dt = datetime.combine(datetime.now().date(), t)
    return dt if dt > datetime.now() else dt + timedelta(days=1)


def next_occurrence(ev, now):
    try:
        base = datetime.strptime(str(ev.get("when", "")), "%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return None
    step = {"daily": 1, "weekly": 7}.get(ev.get("repeat", "none"))
    if step:
        late = now - timedelta(seconds=120)
        if base < late:
            base += timedelta(days=((late - base).days // step) * step)
            while base < late:
                base += timedelta(days=step)
    return base


def fmt_countdown(sec):
    sec = max(0, int(sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m:02d}:{s:02d}"


def fmt_rate(bps):
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / 1024 / 1024:.1f} MB/s"


def resolve_theme(cfg):
    name = cfg.get("theme", "black")
    t = dict(THEME_BASE)
    user = cfg.get("themes", {})
    if name in THEMES:
        t.update(THEMES[name])
    elif isinstance(user, dict) and isinstance(user.get(name), dict):
        t.update(user[name])
    else:
        name = "black"
    style = cfg.get("style", {})
    if isinstance(style, dict):
        t.update({k: v for k, v in style.items() if k in THEME_BASE})
    return name, t


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ helpers --
class Spring:
    """Damped spring. Lower damping = more bounce."""

    def __init__(self, value, stiffness=330.0, damping=19.0):
        self.x = self.target = float(value)
        self.v = 0.0
        self.k = stiffness
        self.d = damping

    def step(self, dt):
        n = int(dt / 0.008) + 1
        h = dt / n
        for _ in range(n):
            self.v += (self.k * (self.target - self.x) - self.d * self.v) * h
            self.x += self.v * h

    def settled(self):
        return abs(self.target - self.x) < 0.3 and abs(self.v) < 3.0

    def snap(self):
        self.x = self.target
        self.v = 0.0


def run_async(parent, program, args, callback=None):
    """Run a command without blocking the UI; callback(stdout_text) when done."""
    p = QProcess(parent)
    env = QProcessEnvironment.systemEnvironment()
    env.insert("LC_ALL", "C")           # stable, English tool output for parsing
    p.setProcessEnvironment(env)
    state = {"done": False}

    def finish(*_):
        if state["done"]:
            return
        state["done"] = True
        out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        if callback:
            callback(out)
        p.deleteLater()

    p.finished.connect(finish)
    p.errorOccurred.connect(finish)
    p.start(program, args)


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def parse_duration(s):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", s or "")
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def send_command(text):
    s = QLocalSocket()
    s.connectToServer(SOCKET_NAME)
    if not s.waitForConnected(300):
        return False
    s.write((text + "\n").encode())
    s.waitForBytesWritten(300)
    s.disconnectFromServer()
    return True


def read_battery():
    if not BATTERY:
        return None
    try:
        with open(BATTERY + "/capacity") as f:
            pct = int(f.read().strip())
        with open(BATTERY + "/status") as f:
            status = f.read().strip()
        return pct, status
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------- island --
class Island(QWidget):
    def __init__(self):
        super().__init__()
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                 | Qt.WindowDoesNotAcceptFocus)
        if load_config().get("x11_bypass_wm", True):
            flags |= Qt.X11BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFixedSize(CANVAS_W, CANVAS_H)
        self.font_family = QApplication.font().family()

        # visual state
        self.mode = "compact"       # what is currently drawn
        self.want = "compact"       # what we want to draw next
        self.payload = None
        self.alpha = 1.0            # content opacity (fades between modes)
        self.fading_out = False
        self.fade_delay = 0.0
        self.sw = Spring(COMPACT_IDLE[0])
        self.sh = Spring(COMPACT_IDLE[1])
        self.expanded = False
        self.hover = False
        self.transient = None
        self._last = time.monotonic()
        self._last_mask = None

        # data state
        self.music = None
        self.first_poll_done = False
        self.art = None
        self.art_url = ""
        self.accent = QColor(235, 235, 235)
        self.volume = None          # (percent, muted)
        self.bat = None             # (pct, status, plugged)
        self.low_warned = set()
        self.timer_end = None
        self.timer_total = 1.0
        self._music_busy = False
        self.nam = QNetworkAccessManager(self)

        self.cfg = load_config()
        if not os.path.exists(CONFIG_PATH):
            self.persist()
        self.page = 0
        self.last_wheel = 0.0
        self.timer_label = ""
        self.timer_color = ORANGE
        self.pomo_active = False
        self.pomo_phase = "focus"
        self.pomo_done = 0
        self.fired = set()
        self.icon_cache = {}
        self.cpu_prev = None
        self.cpu_hist = deque([0.0] * 60, maxlen=60)
        self.ram = 0.0
        self.net_prev = None
        self.net_rate = (0.0, 0.0)
        thermal = sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))
        self.temp_path = thermal[0] if thermal else None
        self.temp = None
        self.led_paths = {
            "Caps Lock": glob.glob("/sys/class/leds/*::capslock/brightness"),
            "Num Lock": glob.glob("/sys/class/leds/*::numlock/brightness"),
        }
        self.leds = {}
        self.xset_fails = 0 if shutil.which("xset") else 99
        self._misc_n = 0
        backlights = sorted(glob.glob("/sys/class/backlight/*"))
        self.backlight = backlights[0] if backlights else None
        self.bright = None
        self.net_state = None
        self.vpn = set()
        self.shelf = []                 # temporary shelf: memory only, gone when the island quits
        self.shelf_msg = ("", 0.0)
        self.dragging = False
        self.hover_pos = None
        self._drag_idx = None
        self._drag_start = QPointF()
        self.file_icons = {}
        self.fi_provider = QFileIconProvider()
        self.theme_bg2 = None
        self.apply_theme()
        self.tray = None
        self.vis_mode = self.cfg.get("mode") if self.cfg.get("mode") in dict(MODES) else "standby"
        if self.effective_mode() != "standby":      # start hidden, no flash
            self.mode = self.want = "hidden"
            self.sw.x = self.sw.target = 40.0
            self.sh.x = self.sh.target = 0.0

        self.place()
        QApplication.primaryScreen().geometryChanged.connect(lambda *_: self.place())

        self.frame = QTimer(self)
        self.frame.timeout.connect(self.tick)

        self.collapse_timer = QTimer(self, singleShot=True, interval=2500)
        self.collapse_timer.timeout.connect(self.collapse)

        self.music_timer = QTimer(self, interval=1000)
        self.music_timer.timeout.connect(self.poll_music)
        self.music_timer.start()

        self.bat_timer = QTimer(self, interval=3000)
        self.bat_timer.timeout.connect(self.poll_battery)
        self.bat_timer.start()

        for name, ms, slot in (("sys_timer", 1000, self.sample_system),
                               ("misc_timer", 300, self.poll_misc),
                               ("net_timer", 2000, self.poll_network),
                               ("event_timer", 15000, self.check_events)):
            t = QTimer(self, interval=ms)
            t.timeout.connect(slot)
            t.start()
            setattr(self, name, t)

        self.start_server()
        self.start_pactl()
        self.start_dbus()
        self.poll_music()
        self.poll_battery()
        self.sample_system()
        self.poll_misc()
        self.poll_network()
        self.check_events()
        self.setup_tray()
        self.wake()

        QApplication.instance().aboutToQuit.connect(self.cleanup)

    # ------------------------------------------------------------ plumbing --
    def place(self):
        g = QApplication.primaryScreen().geometry()
        self.move(g.x() + (g.width() - CANVAS_W) // 2, g.y() + TOP_MARGIN)

    def cleanup(self):
        if getattr(self, "tray", None):
            self.tray.hide()
        for proc in (getattr(self, "pa", None), getattr(self, "dbm", None)):
            if proc and proc.state() != QProcess.NotRunning:
                proc.kill()
        if getattr(self, "server", None):
            self.server.close()

    def wake(self):
        if getattr(self, "frame", None) is None:      # still starting up
            return
        if not self.frame.isActive():
            self._last = time.monotonic()
            self.frame.start(16)

    # ------------------------------------------------------ control socket --
    def start_server(self):
        QLocalServer.removeServer(SOCKET_NAME)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self.on_connection)
        self.server.listen(SOCKET_NAME)

    def on_connection(self):
        s = self.server.nextPendingConnection()

        def read():
            for line in bytes(s.readAll()).decode("utf-8", "replace").splitlines():
                self.command(line.strip())

        s.readyRead.connect(read)
        s.disconnected.connect(s.deleteLater)

    def command(self, line):
        try:
            parts = shlex.split(line)
        except ValueError:
            return
        if not parts:
            return
        cmd, args = parts[0], parts[1:]
        if cmd == "timer":
            secs = parse_duration(args[0] if args else "")
            if secs:
                self.start_timer(secs)
        elif cmd == "toast":
            self.show_toast(" ".join(args) or "Hello")
        elif cmd == "cancel":
            self.cancel_timer()
        elif cmd == "toggle":
            self.toggle_expanded()
        elif cmd == "page":
            name = args[0] if args else "home"
            if name in PAGES and self.effective_mode() != "closed":
                self.expanded = True
                self.transient = None
                self.set_page(PAGES.index(name))
                if not self.hover:
                    self.collapse_timer.start(6000)     # opened by command: close by itself
                self.wake()
        elif cmd in ("pomodoro", "pomo"):
            sub = args[0] if args else ""
            if sub == "stop" or (not sub and self.pomo_active):
                self.pomo_stop()
            elif sub == "skip" and self.pomo_active:
                self.pomo_next()
            elif not self.pomo_active:
                self.pomo_start()
        elif cmd in ("ctf", "remind") and len(args) >= 2:
            repeat = "none"
            if args[-1] in ("daily", "weekly"):
                repeat = args.pop()
            self.add_event(cmd, args[0], " ".join(args[1:]), repeat)
        elif cmd == "forget" and args:
            self.forget_event(" ".join(args))
        elif cmd == "reload":
            self.cfg = load_config()
            self.show_toast("Config reloaded", 2.0)
        elif cmd == "theme":
            self.set_theme(args[0] if args else "next")
        elif cmd == "style":
            self.set_style(args)
        elif cmd == "shelf":
            sub = args[0] if args else ""
            if sub == "add":
                self.add_files(args[1:], announce=True)
            elif sub == "paste":
                self.paste_files(announce=True)
            elif sub == "clear":
                self.shelf_clear()
                self.show_toast("Shelf cleared", 2.0)
            elif sub == "copy":
                self.shelf_copy(self.shelf)
                self.show_toast(self.shelf_msg[0], 2.5)
            elif self.effective_mode() != "closed":
                self.expanded = True
                self.transient = None
                self.set_page(PAGES.index("shelf"))
                if not self.hover:
                    self.collapse_timer.start(6000)
                self.wake()
        elif cmd == "mode":
            self.set_mode(args[0] if args else "cycle")
        elif cmd == "quit":
            QApplication.quit()

    # ------------------------------------------------------------- events --
    def post(self, kind, seconds, **data):
        """Show a temporary activity (notification, volume, ...)."""
        is_new = (self.transient is None or self.transient["kind"] != kind
                  or kind in ("notif", "toast", "track"))
        self.transient = dict(data, kind=kind, until=time.monotonic() + seconds)
        if is_new:                      # little bounce so it feels alive
            self.sh.v += 70
            self.sw.v += 140
        self.wake()

    def show_toast(self, text, seconds=3.0, color=None, force=False):
        self.post("toast", seconds, text=text, color=color, force=force)

    def persist(self):
        try:
            save_config(self.cfg)
        except OSError:
            pass

    def start_timer(self, seconds):
        self.pomo_active = False
        self.timer_label, self.timer_color = "", ORANGE
        self.timer_total = float(seconds)
        self.timer_end = time.time() + seconds
        self.show_toast(f"Timer set · {fmt_time(seconds)}", 2.0)

    def cancel_timer(self):
        self.timer_end = None
        self.pomo_active = False
        self.show_toast("Timer cancelled", 2.0)

    # ---- pomodoro
    def begin_phase(self):
        secs = POMO[self.pomo_phase]
        self.timer_total = float(secs)
        self.timer_end = time.time() + secs
        self.timer_label = {"focus": "Focus", "break": "Break", "long": "Long break"}[self.pomo_phase]
        self.timer_color = ORANGE if self.pomo_phase == "focus" else GREEN

    def pomo_start(self):
        self.pomo_active = True
        self.pomo_phase = "focus"
        self.pomo_done = 0
        self.begin_phase()
        self.show_toast("Focus · 25:00", 2.5, ORANGE)

    def pomo_next(self):
        if self.pomo_phase == "focus":
            self.pomo_done += 1
            self.pomo_phase = "long" if self.pomo_done % 4 == 0 else "break"
        else:
            self.pomo_phase = "focus"
        self.begin_phase()
        self.wake()

    def pomo_stop(self):
        self.pomo_active = False
        self.timer_end = None
        self.timer_label = ""
        self.show_toast("Pomodoro stopped", 2.0)

    # ---- visibility mode (auto / standby / popup / closed)
    def in_quiet_hours(self):
        q = self.cfg.get("quiet_hours")
        if not (isinstance(q, list) and len(q) == 2):
            return False
        try:
            a = datetime.strptime(str(q[0]), "%H:%M").time()
            b = datetime.strptime(str(q[1]), "%H:%M").time()
        except ValueError:
            return False
        now = datetime.now().time()
        return (a <= now < b) if a <= b else (now >= a or now < b)

    def effective_mode(self):
        """What the island actually does right now: standby, popup or closed."""
        if self.vis_mode != "auto":
            return self.vis_mode
        if self.in_quiet_hours():
            return "closed"
        playing = self.music is not None and self.music["status"] == "Playing"
        busy = bool(self.timer_end) or playing or self.expanded
        return "standby" if busy else "popup"

    def set_mode(self, key):
        keys = [k for k, _ in MODES]
        if key == "cycle":
            key = keys[(keys.index(self.vis_mode) + 1) % len(keys)]
        if key not in keys:
            return
        self.vis_mode = key
        self.cfg["mode"] = key
        self.persist()
        if key == "closed":
            self.expanded = False
        self.transient = None
        self.show_toast(f"Mode · {dict(MODES)[key]}", 1.8, MODE_COLORS[key], force=True)
        self.update_tray()
        self.wake()

    def fill_menu(self, m):
        for child in m.findChildren(QMenu):
            child.deleteLater()
        m.clear()
        m.addSection("Mode")
        grp = QActionGroup(m)
        grp.setExclusive(True)
        for key, label in MODES:
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(key == self.vis_mode)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self.set_mode(k))
        themes = m.addMenu("Theme")
        tgrp = QActionGroup(themes)
        tgrp.setExclusive(True)
        for name in self.theme_names():
            a = themes.addAction(THEMES.get(name, {}).get("label", name))
            a.setCheckable(True)
            a.setChecked(name == self.theme_name)
            tgrp.addAction(a)
            a.triggered.connect(lambda _=False, n=name: self.set_theme(n))
        m.addSeparator()
        for label, secs in (("Timer · 5 min", 300), ("Timer · 15 min", 900),
                            ("Timer · 25 min (focus)", 1500)):
            m.addAction(label).triggered.connect(lambda _=False, s=secs: self.start_timer(s))
        if self.timer_end:
            m.addAction("Cancel timer").triggered.connect(self.cancel_timer)
        pomo = m.addAction("Pomodoro · stop" if self.pomo_active else "Pomodoro · start (25/5)")
        pomo.triggered.connect(self.pomo_stop if self.pomo_active else self.pomo_start)
        if self.shelf:
            m.addAction(f"Clear shelf ({len(self.shelf)})").triggered.connect(self.shelf_clear)
        m.addSeparator()
        m.addAction("Quit").triggered.connect(QApplication.quit)

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.setPen(QPen(QColor(235, 235, 235), 4))
        q.setBrush(QColor(20, 20, 20))
        q.drawRoundedRect(QRectF(6, 20, 52, 24), 12, 12)
        q.end()
        self.tray = QSystemTrayIcon(QIcon(pm), self)
        self.tray_menu = QMenu()
        self.tray_menu.aboutToShow.connect(lambda: self.fill_menu(self.tray_menu))
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.on_tray)
        self.tray.show()
        self.update_tray()

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:       # left click = next mode
            self.set_mode("cycle")

    def update_tray(self):
        if self.tray:
            self.tray.setToolTip(f"Dynamic Island · {dict(MODES)[self.vis_mode]}")

    # ---- theme
    def apply_theme(self):
        name, t = resolve_theme(self.cfg)
        self.theme_name = name

        def color(key, default):
            c = QColor(str(t.get(key) or ""))
            return c if c.isValid() else QColor(default)

        def num(key, default, lo, hi):
            try:
                v = float(t.get(key, default))
            except (TypeError, ValueError):
                v = default
            return max(lo, min(hi, v))

        def alpha(c, a):
            return QColor(c.red(), c.green(), c.blue(), a)

        text = color("text", "#ffffff")
        self.c_text = text
        self.c_sub, self.c_sub2, self.c_soft = alpha(text, 140), alpha(text, 190), alpha(text, 170)
        self.c_dim, self.c_btn = alpha(text, 60), alpha(text, 26)
        border = color("border", "#ffffff")
        border.setAlphaF(num("border_opacity", 0.08, 0.0, 1.0))
        self.c_border = border
        bg = color("bg", "#000000")
        bg.setAlphaF(num("opacity", 1.0, 0.05, 1.0))
        self.theme_bg = bg
        bg2 = QColor(str(t.get("bg2") or ""))
        if bg2.isValid():
            bg2.setAlphaF(bg.alphaF())
            self.theme_bg2 = bg2
        else:
            self.theme_bg2 = None
        self.theme_tint = num("tint", 0.0, 0.0, 1.0)
        self.theme_accent = color("accent", text.name())
        self.theme_art_accent = bool(t.get("art_accent", True))
        self.corner = num("corner", 34, 4, 60)
        self.font_family = str(t.get("font") or "") or QApplication.font().family()
        self.bg_cur = QColor(bg)
        self.set_art(self.art)              # re-pick the accent for this theme

    def theme_names(self):
        user = self.cfg.get("themes", {})
        extra = [n for n in user if n not in THEMES] if isinstance(user, dict) else []
        return list(THEMES) + extra

    def theme_label(self):
        return THEMES.get(self.theme_name, {}).get("label", self.theme_name)

    def set_theme(self, name, toast=True):
        names = self.theme_names()
        if name in ("next", "cycle"):
            i = names.index(self.theme_name) if self.theme_name in names else -1
            name = names[(i + 1) % len(names)]
        if name not in names:
            self.show_toast(f"Unknown theme · {name}", 2.5, RED)
            return
        self.cfg["theme"] = name
        self.cfg["style"] = {}              # tweaks belong to one theme
        self.persist()
        self.apply_theme()
        if toast:
            self.show_toast(f"Theme · {self.theme_label()}", 1.8)
        self.wake()

    def set_style(self, args):
        if args and args[0] == "reset":
            self.cfg["style"] = {}
            self.persist()
            self.apply_theme()
            self.show_toast("Style reset", 2.0)
            return
        if len(args) < 2 or args[0] not in THEME_BASE:
            self.show_toast("style: " + " | ".join(THEME_BASE), 4.0, RED)
            return
        key, val = args[0], " ".join(args[1:])
        if key in STYLE_NUMERIC:
            try:
                val = float(val)
            except ValueError:
                self.show_toast(f"{key} needs a number", 2.5, RED)
                return
        elif key == "art_accent":
            val = val.lower() in ("1", "true", "yes", "on")
        elif key in ("bg", "bg2", "text", "accent", "border") and val and not QColor(val).isValid():
            self.show_toast(f"Bad color · {val}", 2.5, RED)
            return
        self.cfg["style"][key] = val
        self.persist()
        self.apply_theme()
        self.show_toast(f"{key} = {val}", 2.0)

    # ---- file shelf (temporary: kept in memory only)
    def shelf_note(self, text, secs=3.0):
        self.shelf_msg = (text, time.monotonic() + secs)
        self.wake()

    def make_item(self, pth):
        isdir = os.path.isdir(pth)
        try:
            info = f"{len(os.listdir(pth))} items" if isdir else human_size(os.path.getsize(pth))
        except OSError:
            info = "Folder" if isdir else ""
        return {"path": pth, "name": os.path.basename(pth.rstrip("/")) or pth,
                "info": info, "added": time.time()}

    def add_files(self, paths, announce=False):
        added = full = dup = 0
        for pth in paths:
            pth = os.path.abspath(os.path.expanduser(pth))
            if not os.path.exists(pth):
                continue
            if any(i["path"] == pth for i in self.shelf):
                dup += 1
            elif len(self.shelf) >= MAX_SHELF:
                full += 1
            else:
                self.shelf.append(self.make_item(pth))
                added += 1
        if added:
            msg = f"Added {added}" + (f" · {full} skipped (shelf full)" if full else "")
        elif full:
            msg = f"Shelf full ({MAX_SHELF}) · remove one first"
        elif dup:
            msg = "Already on the shelf"
        else:
            msg = "Nothing to add"
        self.shelf_note(msg)
        if announce:
            self.show_toast(msg, 2.5, GREEN if added else RED)
        return added

    def paste_files(self, announce=False):
        """Add files that were copied (Ctrl+C) in Dolphin / any file manager."""
        md = QApplication.clipboard().mimeData()
        paths = []
        if md is not None:
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if not paths and md.hasText():
                for line in md.text().splitlines():
                    line = line.strip()
                    if line.startswith("file://"):
                        line = QUrl(line).toLocalFile()
                    if line.startswith("/") and os.path.exists(line):
                        paths.append(line)
        if not paths:
            self.shelf_note("Clipboard has no files · copy some first")
            if announce:
                self.show_toast("Clipboard has no files", 2.5, RED)
            return 0
        return self.add_files(paths, announce=announce)

    def prune_shelf(self):
        try:
            exp = float(self.cfg.get("shelf_expire_minutes", 0) or 0)
        except (TypeError, ValueError):
            exp = 0
        now = time.time()
        keep = [i for i in self.shelf
                if os.path.exists(i["path"]) and not (exp and now - i["added"] > exp * 60)]
        if len(keep) != len(self.shelf):
            self.shelf = keep
            self.wake()

    def shelf_clear(self):
        self.shelf = []
        self.shelf_note("Shelf cleared")

    def shelf_copy(self, items):
        items = [i for i in items if os.path.exists(i["path"])]
        if not items:
            self.shelf_note("Nothing to copy")
            return
        urls = [QUrl.fromLocalFile(i["path"]) for i in items]
        md = QMimeData()
        md.setUrls(urls)
        uris = "\n".join(bytes(u.toEncoded()).decode() for u in urls)
        md.setData("x-special/gnome-copied-files", ("copy\n" + uris).encode())
        md.setData("application/x-kde-cutselection", b"0")      # 0 = copy, not cut
        md.setText("\n".join(i["path"] for i in items))
        QApplication.clipboard().setMimeData(md)
        label = items[0]["name"] if len(items) == 1 else f"{len(items)} items"
        self.shelf_note(f"Copied {label} · Ctrl+V to paste")

    def icon_pixmap(self, path):
        if path not in self.file_icons:
            ic = self.fi_provider.icon(QFileInfo(path))
            self.file_icons[path] = None if ic.isNull() else ic.pixmap(32, 32)
        return self.file_icons[path]

    def start_drag(self, items):
        items = [i for i in items if os.path.exists(i["path"])]
        if not items:
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(i["path"]) for i in items])
        drag = QDrag(self)
        drag.setMimeData(md)
        pm = self.icon_pixmap(items[0]["path"])
        if pm:
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        drag.exec(Qt.CopyAction)
        self._drag_idx = None
        self.hover = False
        self.hover_pos = None
        if self.expanded:
            self.collapse_timer.start(2500)
        self.wake()

    # ---- pages
    def set_page(self, i):
        if i == self.page:
            return
        self.page = i
        self.alpha = 0.0                # quick fade-in of the new page
        self.fade_delay = 0.0
        self.wake()

    # ---- CTF events + reminders
    def add_event(self, kind, name, when_s, repeat="none"):
        dt = parse_when(when_s)
        if not dt or not name:
            self.show_toast("Bad date · use YYYY-MM-DD HH:MM", 4.0, RED)
            return
        self.cfg["events"].append({"name": name, "when": dt.strftime("%Y-%m-%d %H:%M"),
                                   "kind": kind, "repeat": repeat})
        self.persist()
        self.show_toast(f"{'CTF' if kind == 'ctf' else 'Reminder'} added · {name}", 3.0, GREEN)

    def forget_event(self, name):
        before = len(self.cfg["events"])
        self.cfg["events"] = [e for e in self.cfg["events"]
                              if not (isinstance(e, dict) and str(e.get("name", "")).lower() == name.lower())]
        self.persist()
        removed = before - len(self.cfg["events"])
        self.show_toast(f"Removed {removed} event(s)" if removed else "No event with that name", 2.5)

    def upcoming(self, kind):
        now = datetime.now()
        best = None
        for ev in self.cfg.get("events", []):
            if not isinstance(ev, dict) or ev.get("kind") != kind:
                continue
            occ = next_occurrence(ev, now)
            if occ and occ >= now and (best is None or occ < best[0]):
                best = (occ, ev)
        return best

    def check_events(self):
        now = datetime.now()
        for ev in self.cfg.get("events", []):
            if not isinstance(ev, dict):
                continue
            occ = next_occurrence(ev, now)
            if not occ:
                continue
            offsets = (1440, 60, 10, 0) if ev.get("kind") == "ctf" else (10, 0)
            for off in offsets:
                at = occ - timedelta(minutes=off)
                key = (ev.get("name"), occ.isoformat(), off)
                if at <= now < at + timedelta(seconds=120) and key not in self.fired:
                    self.fired.add(key)
                    self.fire_event(ev, off)
        self.prune_shelf()
        self.wake()                     # re-evaluate quiet hours / automatic mode

    def fire_event(self, ev, off):
        label = "CTF" if ev.get("kind") == "ctf" else "Reminder"
        if off == 0:
            body = "Starting now"
        elif off >= 1440:
            body = "Starts in 1 day"
        elif off >= 60:
            body = f"Starts in {off // 60} hour"
        else:
            body = f"Starts in {off} min"
        self.post("notif", 8.0, app=label, title=str(ev.get("name", "")), body=body, icon=None)

    # ---- system stats (CPU / RAM / network / temperature)
    def sample_system(self):
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:9]]
            idle, total = v[3] + v[4], sum(v)
            if self.cpu_prev and total > self.cpu_prev[0]:
                busy = 1 - (idle - self.cpu_prev[1]) / (total - self.cpu_prev[0])
                self.cpu_hist.append(max(0.0, min(100.0, busy * 100)))
            self.cpu_prev = (total, idle)
        except (OSError, ValueError, IndexError):
            pass
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, _, val = line.partition(":")
                    if k in ("MemTotal", "MemAvailable"):
                        info[k] = int(val.split()[0])
                    if len(info) == 2:
                        break
            self.ram = 100 * (1 - info["MemAvailable"] / info["MemTotal"])
        except (OSError, ValueError, KeyError, ZeroDivisionError):
            pass
        try:
            rx = tx = 0
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    name, _, data = line.partition(":")
                    if name.strip() == "lo":
                        continue
                    cols = data.split()
                    rx += int(cols[0])
                    tx += int(cols[8])
            now = time.monotonic()
            if self.net_prev:
                dt = max(0.001, now - self.net_prev[2])
                self.net_rate = (max(0.0, (rx - self.net_prev[0]) / dt),
                                 max(0.0, (tx - self.net_prev[1]) / dt))
            self.net_prev = (rx, tx, now)
        except (OSError, ValueError, IndexError):
            pass
        if self.temp_path:
            t = read_int(self.temp_path)
            self.temp = t / 1000 if t else None

    # ---- Caps/Num lock + brightness
    def poll_misc(self):
        self._misc_n += 1
        have_leds = any(self.led_paths.values())
        if have_leds:
            for name, paths in self.led_paths.items():
                if paths:
                    self.set_lock(name, any((read_int(pth) or 0) > 0 for pth in paths))
        elif self.xset_fails < 5 and self._misc_n % 3 == 0:
            self.poll_xset()
        if self.backlight:
            b = read_int(self.backlight + "/brightness")
            mx = read_int(self.backlight + "/max_brightness")
            if b is not None and mx:
                pct = round(100 * b / mx)
                if self.bright is not None and pct != self.bright:
                    self.post("brightness", 1.6, vol=pct, mute=False)
                self.bright = pct

    def set_lock(self, name, on):
        old = self.leds.get(name)
        self.leds[name] = on
        if old is not None and old != on:
            self.show_toast(f"{name} · {'ON' if on else 'off'}", 1.6, ORANGE if on else self.c_sub)

    def poll_xset(self):
        def got(out):
            caps = re.search(r"Caps Lock:\s+(on|off)", out)
            num = re.search(r"Num Lock:\s+(on|off)", out)
            if not caps:
                self.xset_fails += 1
                return
            self.set_lock("Caps Lock", caps.group(1) == "on")
            if num:
                self.set_lock("Num Lock", num.group(1) == "on")

        run_async(self, "xset", ["q"], got)

    # ---- VPN / Wi-Fi / network alerts
    def net_snapshot(self):
        base = "/sys/class/net"
        try:
            names = os.listdir(base)
        except OSError:
            return None
        vpn, wifi, eth = set(), False, False
        for n in names:
            if re.match(r"(tun|tap|wg|ppp|tailscale)", n):
                vpn.add(n)
                continue
            if n == "lo" or n.startswith(("docker", "br-", "veth", "virbr", "vmnet")):
                continue
            try:
                with open(f"{base}/{n}/operstate") as f:
                    state = f.read().strip()
            except OSError:
                continue
            if state != "up":
                continue
            if os.path.isdir(f"{base}/{n}/wireless"):
                wifi = True
            else:
                eth = True
        return vpn, wifi, eth

    def poll_network(self):
        snap = self.net_snapshot()
        if snap is None:
            return
        vpn, wifi, eth = snap
        if self.net_state is not None:
            pvpn, pwifi, peth = self.net_state
            for n in sorted(vpn - pvpn):
                self.announce_vpn(n)
            for n in sorted(pvpn - vpn):
                self.show_toast(f"VPN disconnected · {n}", 3.5, RED)
            online, was = wifi or eth, pwifi or peth
            if online and not was:
                if wifi:
                    self.announce_wifi()
                else:
                    self.show_toast("Ethernet connected", 3.0, GREEN)
            elif was and not online:
                self.show_toast("Network disconnected", 3.5, RED)
        self.net_state = snap
        if vpn != self.vpn:
            self.vpn = vpn
            self.wake()

    def announce_vpn(self, name):
        def got(out):
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
            self.show_toast(f"VPN connected · {m.group(1) if m else name}", 4.0, GREEN)

        run_async(self, "ip", ["-4", "-o", "addr", "show", "dev", name], got)

    def announce_wifi(self):
        def got(out):
            ssid = ""
            for line in out.splitlines():
                if line.startswith("yes:"):
                    ssid = line[4:].replace("\\:", ":")
                    break
            self.show_toast(f"Wi-Fi connected · {ssid}" if ssid else "Wi-Fi connected", 3.5, GREEN)

        run_async(self, "nmcli", ["-t", "-f", "ACTIVE,SSID", "dev", "wifi"], got)

    def toggle_expanded(self):
        if not self.expanded and self.effective_mode() == "closed":
            return                      # closed means closed
        self.expanded = not self.expanded
        self.transient = None
        if self.expanded:
            self.poll_music()
            if not self.hover:
                self.collapse_timer.start(6000)     # opened by command: close by itself
        self.wake()

    def collapse(self):
        if self.expanded and not self.hover:
            self.expanded = False
            self.wake()

    # -------------------------------------------------------------- music --
    def poll_music(self):
        if self._music_busy:
            return
        self._music_busy = True
        run_async(self, "playerctl", ["metadata", "--format", PLAYER_FORMAT], self.on_music)

    def on_music(self, out):
        self._music_busy = False
        parts = out.rstrip("\r\n").split("\t")
        if len(parts) < 7:
            if self.music is not None:
                self.music = None
                self.wake()
            self.first_poll_done = True
            return

        def num(s):
            try:
                return float(s) / 1e6
            except ValueError:
                return 0.0

        status, artist, title, album, pos, length, art_url = parts[:7]
        new = dict(status=status, artist=artist, title=title or "Unknown", album=album,
                   pos=num(pos), length=num(length), t=time.monotonic())
        old = self.music
        self.music = new
        self.load_art(art_url)
        changed = old is None or (old["title"], old["artist"]) != (new["title"], new["artist"])
        if changed and status == "Playing" and not self.expanded and self.first_poll_done:
            self.post("track", 3.5)
        self.first_poll_done = True
        self.wake()

    def music_position(self):
        m = self.music
        if not m:
            return 0.0
        pos = m["pos"]
        if m["status"] == "Playing":
            pos += time.monotonic() - m["t"]
        return min(pos, m["length"]) if m["length"] else pos

    def player(self, *args):
        run_async(self, "playerctl", list(args))
        QTimer.singleShot(250, self.poll_music)

    def load_art(self, url):
        if url == self.art_url:
            return
        self.art_url = url
        self.set_art(None)
        if url.startswith("file://"):
            self.set_art(QPixmap(QUrl(url).toLocalFile()))
        elif url.startswith("http"):
            reply = self.nam.get(QNetworkRequest(QUrl(url)))
            reply.finished.connect(lambda r=reply, u=url: self.art_downloaded(r, u))

    def art_downloaded(self, reply, url):
        data = reply.readAll()
        reply.deleteLater()
        if url != self.art_url:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            self.set_art(pm)

    def set_art(self, pm):
        self.art = None
        self.accent = self.theme_accent
        if pm is not None and not pm.isNull():
            s = pm.scaled(160, 160, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.art = s.copy((s.width() - 160) // 2, (s.height() - 160) // 2, 160, 160)
            if self.theme_art_accent:
                c = self.art.scaled(1, 1, Qt.IgnoreAspectRatio,
                                    Qt.SmoothTransformation).toImage().pixelColor(0, 0)
                h, sat, light, _ = c.getHslF()
                if h >= 0 and sat >= 0.15:
                    self.accent = QColor.fromHslF(h, min(1.0, max(sat, 0.55)),
                                                  min(0.72, max(light, 0.58)))
        self.wake()

    # ------------------------------------------------------------- volume --
    def start_pactl(self):
        self.vol_debounce = QTimer(self, singleShot=True, interval=40)
        self.vol_debounce.timeout.connect(self.query_volume)
        self.pa = QProcess(self)
        self.pa.readyReadStandardOutput.connect(self.on_pactl)
        self.pa.start("pactl", ["subscribe"])
        self.query_volume(silent=True)

    def on_pactl(self):
        data = bytes(self.pa.readAllStandardOutput()).decode("utf-8", "replace")
        if re.search(r"'change' on sink #", data):
            self.vol_debounce.start()

    def query_volume(self, silent=False):
        def got_volume(out):
            m = re.search(r"(\d+)%", out)
            if not m:
                return
            vol = int(m.group(1))

            def got_mute(out2):
                muted = "yes" in out2.lower()
                new = (vol, muted)
                if new != self.volume:
                    first = self.volume is None
                    self.volume = new
                    if not silent and not first:
                        self.post("volume", 1.8, vol=vol, mute=muted)

            run_async(self, "pactl", ["get-sink-mute", "@DEFAULT_SINK@"], got_mute)

        run_async(self, "pactl", ["get-sink-volume", "@DEFAULT_SINK@"], got_volume)

    # ------------------------------------------------------------ battery --
    def poll_battery(self):
        b = read_battery()
        if not b:
            return
        pct, status = b
        plugged = status in ("Charging", "Full", "Not charging")
        if self.bat is not None:
            was_plugged = self.bat[2]
            if plugged and not was_plugged:
                label = "Fully charged" if status == "Full" else "Charging"
                self.post("battery", 4.0, pct=pct, charging=True, label=label)
            elif was_plugged and not plugged:
                self.post("battery", 3.0, pct=pct, charging=False, label="On battery")
        if not plugged:
            for level in (20, 10):
                if pct <= level and level not in self.low_warned:
                    self.low_warned.add(level)
                    self.post("battery", 5.0, pct=pct, charging=False, label="Low battery")
                    break
        else:
            self.low_warned.clear()
        self.bat = (pct, status, plugged)

    # ------------------------------------------------------ notifications --
    def start_dbus(self):
        self._dbuf = ""
        self._ncol = None
        self.dbm = QProcess(self)
        self.dbm.readyReadStandardOutput.connect(self.on_dbus)
        self.dbm.start("dbus-monitor", [
            "--session",
            "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
        ])

    def on_dbus(self):
        self._dbuf += bytes(self.dbm.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._dbuf = self._dbuf.split("\n")
        for ln in lines:
            if ln.startswith("method call") and "member=Notify" in ln:
                self._ncol = []
            elif self._ncol is not None:
                s = ln.strip()
                if s.startswith(("array [", "method call", "method return", "signal")):
                    self.handle_notify(self._ncol)
                    self._ncol = None
                else:
                    self._ncol.append(ln)

    def handle_notify(self, lines):
        items = []
        for ln in lines:
            s = ln.strip()
            if s.startswith('string "'):
                items.append(s[len('string "'):])
            elif s.startswith(("uint32", "int32")):
                continue
            elif items:                 # continuation of a multi-line string
                items[-1] += "\n" + ln.rstrip()
        strings = [v[:-1] if v.endswith('"') else v for v in items]
        if len(strings) < 3:
            return
        app, icon, summary = strings[0], strings[1], strings[2]
        body = strings[3] if len(strings) > 3 else ""
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).replace("\n", " ").strip()
        summary = html.unescape(re.sub(r"<[^>]+>", "", summary)).strip()
        if not summary and not body:
            return
        pm = None
        if icon:
            if icon.startswith("/"):
                pm = QPixmap(icon)
            elif icon.startswith("file://"):
                pm = QPixmap(QUrl(icon).toLocalFile())
            else:
                ic = QIcon.fromTheme(icon)
                pm = ic.pixmap(64, 64) if not ic.isNull() else None
        if (pm is None or pm.isNull()) and app:
            ic = QIcon.fromTheme(app.lower())
            pm = ic.pixmap(64, 64) if not ic.isNull() else None
        self.post("notif", 5.5, app=app, title=summary or app, body=body, icon=pm)

    # ---------------------------------------------------------- animation --
    def desired(self):
        t = self.transient
        em = self.effective_mode()
        if t:
            if time.monotonic() >= t["until"]:
                self.transient = None
            elif em != "closed" or t.get("force"):
                return t["kind"], t
        if em == "closed":
            return "hidden", None
        if self.expanded:
            return "expanded", None
        return ("compact" if em == "standby" else "hidden"), None

    def compact_active(self):
        return bool(self.timer_end) or (
            self.music is not None and self.music["status"] in ("Playing", "Paused"))

    def size_for(self, mode):
        if mode == "compact":
            w, h = COMPACT_ACTIVE if self.compact_active() else COMPACT_IDLE
            return (w + 8 if self.hover else w), h
        return SIZES[mode]

    def pill_rect(self):
        w, h = max(0.0, self.sw.x), max(0.0, self.sh.x)
        return QRectF((CANVAS_W - w) / 2, 0, w, h)

    def tick(self):
        now = time.monotonic()
        dt = min(now - self._last, 0.04)
        self._last = now

        if self.timer_end and time.time() >= self.timer_end:
            self.timer_end = None
            if self.pomo_active:
                was_focus = self.pomo_phase == "focus"
                self.pomo_next()
                msg = "Focus done · take a break" if was_focus else "Break over · back to focus"
                self.show_toast(msg, 6.0, self.timer_color)
            else:
                self.show_toast("Timer finished", 6.0)

        want, payload = self.desired()
        self.want = want
        if want != "hidden" and not self.isVisible():
            self._last_mask = None
            self.place()
            self.show()                 # pop out of hiding
        if want == self.mode and not self.fading_out:
            self.payload = payload
        elif not self.fading_out:
            self.fading_out = True

        # fade old content out -> resize -> fade new content in
        if self.fading_out:
            self.alpha = max(0.0, self.alpha - dt / 0.09)
            if self.alpha <= 0.0:
                self.mode = self.want
                self.payload = payload
                self.fading_out = False
                self.fade_delay = 0.10
        elif self.fade_delay > 0:
            self.fade_delay -= dt
        elif self.alpha < 1.0:
            self.alpha = min(1.0, self.alpha + dt / 0.18)

        tw, th = self.size_for(self.mode)
        self.sw.target, self.sh.target = tw, th
        for sp in (self.sw, self.sh):
            sp.step(dt)
            if sp.settled():
                sp.snap()

        r = self.pill_rect().adjusted(-1, -1, 1, 1).toAlignedRect()
        if r != self._last_mask:
            self._last_mask = r
            if self.cfg.get("input_mask", True):
                self.setMask(QRegion(r))    # clicks outside the pill pass through
            elif self.mask() != QRegion():
                self.clearMask()
        self.update()

        # smoothly follow the background color (themes, album tint)
        tgt = QColor(self.theme_bg)
        if self.theme_tint > 0 and self.art and self.mode != "hidden":
            k, a = self.theme_tint, self.accent
            tgt = QColor(int(tgt.red() * (1 - k) + a.red() * k), int(tgt.green() * (1 - k) + a.green() * k),
                         int(tgt.blue() * (1 - k) + a.blue() * k), tgt.alpha())
        cur, f = self.bg_cur, 1 - math.exp(-dt * 6)
        diffs = (tgt.red() - cur.red(), tgt.green() - cur.green(), tgt.blue() - cur.blue(),
                 tgt.alpha() - cur.alpha())
        if max(abs(d) for d in diffs) < 2:
            new = QColor(tgt)
        else:
            new = QColor(*(int(round(c + d * f)) for c, d in zip(
                (cur.red(), cur.green(), cur.blue(), cur.alpha()), diffs)))
        bg_busy = not (new == cur)
        self.bg_cur = new

        if (self.mode == "hidden" and self.want == "hidden" and not self.fading_out
                and self.sw.settled() and self.sh.settled() and self.isVisible()):
            self.hide()                 # fully tucked away: no window, no clicks, no CPU

        animating = (not self.sw.settled() or not self.sh.settled() or self.fading_out
                     or self.fade_delay > 0 or self.alpha < 1.0 or self.want != self.mode or bg_busy)
        playing = self.music is not None and self.music["status"] == "Playing"
        steady = bool(self.transient) or bool(self.timer_end) or self.mode == "expanded" or (
            playing and self.mode in ("compact", "track"))
        if animating:
            self.frame.setInterval(16)
        elif steady:
            self.frame.setInterval(40)
        else:
            self.frame.stop()

    # -------------------------------------------------------------- input --
    def enterEvent(self, e):
        self.hover = True
        self.collapse_timer.stop()
        self.wake()

    def leaveEvent(self, e):
        self.hover = False
        self.hover_pos = None
        if self.expanded:
            self.collapse_timer.start(2500)
        self.wake()

    def layout(self, r):
        x, y, w = r.x(), r.y(), r.width()
        cx = x + w / 2
        pad = 20
        return {
            "art": QRectF(x + pad, y + 16, 64, 64),
            "bar": QRectF(x + pad, y + 94, w - 2 * pad, 5),
            "prev": QRectF(cx - 70 - 16, y + 118, 32, 32),
            "play": QRectF(cx - 20, y + 116, 40, 36),
            "next": QRectF(cx + 70 - 16, y + 118, 32, 32),
        }

    def dot_rects(self, r):
        n = len(PAGES)
        x0 = r.center().x() - n * 9
        return [QRectF(x0 + i * 18, r.bottom() - 20, 18, 16) for i in range(n)]

    def mode_chip(self, r):
        return QRectF(r.right() - 86, r.bottom() - 22, 70, 18)

    def theme_chip(self, r):
        return QRectF(r.x() + 16, r.bottom() - 22, 96, 18)

    def shelf_rects(self, r):
        return {"paste": QRectF(r.right() - 22 - 52 - 6 - 66 - 6 - 48, r.y() + 10, 48, 18),
                "copy": QRectF(r.right() - 22 - 52 - 6 - 66, r.y() + 10, 66, 18),
                "clear": QRectF(r.right() - 22 - 52, r.y() + 10, 52, 18)}

    def shelf_rows(self, r):
        rows = []
        for i in range(len(self.shelf)):
            rect = QRectF(r.x() + 16, r.y() + 34 + i * 23, r.width() - 32, 21)
            rows.append((rect, QRectF(rect.right() - 26, rect.y(), 24, rect.height())))
        return rows

    def pomo_buttons(self, r):
        return {"btn": QRectF(r.x() + 22, r.y() + 118, 92, 28),
                "skip": QRectF(r.x() + 122, r.y() + 118, 56, 28)}

    def launch_cells(self, r):
        items = [i for i in self.cfg.get("launchers", []) if isinstance(i, dict) and i.get("cmd")][:8]
        cols, cw, ch = 4, (r.width() - 40) / 4, 64
        cells = []
        for i, item in enumerate(items):
            row, col = divmod(i, cols)
            cells.append((QRectF(r.x() + 20 + col * cw, r.y() + 18 + row * (ch + 6), cw, ch), item))
        return cells

    def launch(self, item):
        try:
            parts = shlex.split(item.get("cmd", ""))
        except ValueError:
            parts = []
        if not parts:
            return
        QProcess.startDetached(parts[0], parts[1:])
        self.expanded = False
        self.show_toast(f"Launching {item.get('name', parts[0])}", 1.6)

    def click_expanded(self, pos):
        r = self.pill_rect()
        for i, rect in enumerate(self.dot_rects(r)):
            if rect.contains(pos):
                self.set_page(i)
                return True
        if self.mode_chip(r).adjusted(-4, -4, 4, 4).contains(pos):
            self.set_mode("cycle")
            return True
        if self.theme_chip(r).adjusted(-4, -4, 4, 4).contains(pos):
            self.set_theme("next", toast=False)
            return True
        page = PAGES[self.page]
        if page == "home" and self.music:
            z = self.layout(r)
            if z["prev"].contains(pos):
                self.player("previous")
            elif z["play"].contains(pos):
                self.music["status"] = "Paused" if self.music["status"] == "Playing" else "Playing"
                self.music["pos"], self.music["t"] = self.music_position(), time.monotonic()
                self.player("play-pause")
            elif z["next"].contains(pos):
                self.player("next")
            elif z["bar"].adjusted(0, -10, 0, 10).contains(pos) and self.music["length"]:
                frac = min(1.0, max(0.0, (pos.x() - z["bar"].x()) / z["bar"].width()))
                secs = frac * self.music["length"]
                self.music["pos"], self.music["t"] = secs, time.monotonic()
                self.player("position", f"{secs:.1f}")
            else:
                return False
            return True
        if page == "focus":
            b = self.pomo_buttons(r)
            if b["btn"].contains(pos):
                self.pomo_stop() if self.pomo_active else self.pomo_start()
                return True
            if self.pomo_active and b["skip"].contains(pos):
                self.pomo_next()
                return True
        if page == "shelf":
            sr = self.shelf_rects(r)
            if sr["paste"].contains(pos):
                self.paste_files()
                return True
            if sr["copy"].contains(pos):
                self.shelf_copy(self.shelf)
                return True
            if sr["clear"].contains(pos):
                self.shelf_clear()
                return True
            for i, (rect, xr) in enumerate(self.shelf_rows(r)):
                if xr.contains(pos):
                    del self.shelf[i]
                    self.wake()
                    return True
                if rect.contains(pos):          # click = copy, drag = drag the file out
                    self._drag_idx = i
                    self._drag_start = QPointF(pos)
                    return True
        if page == "launch":
            for rect, item in self.launch_cells(r):
                if rect.contains(pos):
                    self.launch(item)
                    return True
        return False

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.RightButton:
            self.show_menu(e.globalPosition().toPoint())
            return
        if e.button() == Qt.MiddleButton:
            self.player("play-pause")
            return
        if self.transient and self.mode == self.transient["kind"]:
            self.transient = None       # dismiss
            self.wake()
            return
        if self.mode == "expanded":
            if not self.click_expanded(pos):
                self.toggle_expanded()
            self.wake()
            return
        self.toggle_expanded()

    def mouseReleaseEvent(self, e):
        if self._drag_idx is not None and e.button() == Qt.LeftButton:
            i, self._drag_idx = self._drag_idx, None
            if i < len(self.shelf):
                self.shelf_copy([self.shelf[i]])

    def mouseMoveEvent(self, e):
        self.hover_pos = e.position()
        if self._drag_idx is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            return
        idx, self._drag_idx = self._drag_idx, None
        if idx >= len(self.shelf):
            return
        everything = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        self.start_drag(list(self.shelf) if everything else [self.shelf[idx]])

    # drag & drop INTO the island: it opens the shelf so you have a big target
    def dragEnterEvent(self, e):
        if DEBUG:
            print("[island] dragEnter, formats:", e.mimeData().formats(), flush=True)
        if self.effective_mode() == "closed" or not e.mimeData().hasUrls():
            e.ignore()
            return
        self.dragging = True
        self.collapse_timer.stop()
        self.transient = None
        self.expanded = True
        self.set_page(PAGES.index("shelf"))
        e.acceptProposedAction()
        self.wake()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.dragging = False
        if self.expanded:
            self.collapse_timer.start(2500)
        self.wake()

    def dropEvent(self, e):
        if DEBUG:
            print("[island] drop:", [u.toString() for u in e.mimeData().urls()], flush=True)
        self.dragging = False
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()
        self.collapse_timer.start(6000)
        self.wake()

    def wheelEvent(self, e):
        if self.mode != "expanded":
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        now = time.monotonic()
        if not d or now - self.last_wheel < 0.25:
            return
        self.last_wheel = now
        self.set_page((self.page + (-1 if d > 0 else 1)) % len(PAGES))

    def show_menu(self, gpos):
        m = QMenu()
        self.fill_menu(m)
        m.exec(gpos)

    # ------------------------------------------------------------ drawing --
    def font(self, size, bold=False):
        f = QFont(self.font_family)
        if self.font_family.lower() == "monospace":
            f.setStyleHint(QFont.Monospace)
        f.setPointSizeF(size)
        f.setBold(bold)
        return f

    def text(self, p, rect, s, size=11, bold=False, color=None,
             align=Qt.AlignVCenter | Qt.AlignLeft):
        f = self.font(size, bold)
        p.setFont(f)
        p.setPen(color if color is not None else self.c_text)
        s = QFontMetrics(f).elidedText(s, Qt.ElideRight, int(rect.width()))
        p.drawText(rect, align, s)

    def draw_art(self, p, rect, radius):
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(path, Qt.IntersectClip)
        if self.art:
            p.drawPixmap(rect.toRect(), self.art)
        else:
            p.fillRect(rect, QColor(48, 48, 54))
            self.text(p, rect, "♪", rect.height() * 0.45, False, self.c_sub, Qt.AlignCenter)
        p.restore()

    def draw_eq(self, p, right, cy, playing):
        t = time.monotonic()
        p.setPen(Qt.NoPen)
        p.setBrush(self.accent)
        for i in range(4):
            h = 4 + (11 * (0.5 + 0.5 * math.sin(t * (6 + i * 1.7) + i * 1.3)) if playing else 0)
            p.drawRoundedRect(QRectF(right - 4 * 5 + i * 5, cy - h / 2, 3, h), 1.5, 1.5)

    def draw_compact(self, p, r):
        cy = r.center().y()
        music = self.music is not None and self.music["status"] in ("Playing", "Paused")
        left = QRectF(r.x() + 12, cy - 10, 20, 20)
        remaining = max(0.0, self.timer_end - time.time()) if self.timer_end else 0

        if music:
            self.draw_art(p, left, 6)
        elif self.timer_end:
            ring = left.adjusted(2, 2, -2, -2)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(self.c_dim, 2.5))
            p.drawEllipse(ring)
            p.setPen(QPen(self.timer_color, 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-(remaining / self.timer_total) * 360 * 16))

        if self.timer_end:
            self.text(p, QRectF(r.right() - 64, r.y(), 52, r.height()), fmt_time(remaining + 0.99),
                      10.5, True, self.timer_color, Qt.AlignVCenter | Qt.AlignRight)
        elif music:
            self.draw_eq(p, r.right() - 14, cy, self.music["status"] == "Playing")
        elif self.vpn:                  # small "VPN up" badge while idle
            p.setPen(Qt.NoPen)
            p.setBrush(GREEN)
            p.drawEllipse(QRectF(r.center().x() - 20, cy - 3, 6, 6))
            self.text(p, QRectF(r.center().x() - 10, r.y(), 40, r.height()), "VPN", 9, True, self.c_sub)
        if self.shelf and not music and not self.timer_end:     # files waiting on the shelf
            c = QPointF(r.right() - 22, cy)
            p.setPen(Qt.NoPen)
            p.setBrush(self.c_dim)
            p.drawEllipse(c, 8, 8)
            self.text(p, QRectF(c.x() - 8, c.y() - 8, 16, 16), str(len(self.shelf)), 8, True,
                      self.c_text, Qt.AlignCenter)

    def draw_track(self, p, r):
        cy = r.center().y()
        m = self.music or {}
        self.draw_art(p, QRectF(r.x() + 10, cy - 19, 38, 38), 9)
        self.text(p, QRectF(r.x() + 58, cy - 21, r.width() - 110, 22), m.get("title", ""), 11.5, True)
        self.text(p, QRectF(r.x() + 58, cy + 1, r.width() - 110, 18), m.get("artist", ""), 10, False, self.c_sub)
        self.draw_eq(p, r.right() - 18, cy, m.get("status") == "Playing")

    def draw_notif(self, p, r):
        t = self.payload or {}
        cy = r.center().y()
        icon = QRectF(r.x() + 16, cy - 22, 44, 44)
        pm = t.get("icon")
        path = QPainterPath()
        path.addRoundedRect(icon, 11, 11)
        p.save()
        p.setClipPath(path, Qt.IntersectClip)
        if pm is not None and not pm.isNull():
            p.fillRect(icon, QColor(36, 36, 40))
            p.drawPixmap(icon.toRect(), pm)
        else:
            p.fillRect(icon, QColor(10, 132, 255))
            self.text(p, icon, "🔔", 15, False, self.c_text, Qt.AlignCenter)
        p.restore()
        x = icon.right() + 12
        w = r.right() - 16 - x
        self.text(p, QRectF(x, r.y() + 10, w, 14), t.get("app", ""), 8.5, False, self.c_sub)
        self.text(p, QRectF(x, r.y() + 24, w, 20), t.get("title", ""), 11.5, True)
        if t.get("body"):
            self.text(p, QRectF(x, r.y() + 45, w, 20), t["body"], 10, False, self.c_sub2)

    def draw_battery_icon(self, p, x, cy, pct, color, bolt):
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self.c_soft, 1.5))
        p.drawRoundedRect(QRectF(x, cy - 7, 28, 14), 4, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(self.c_soft)
        p.drawRoundedRect(QRectF(x + 29.5, cy - 3, 2.5, 6), 1, 1)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x + 2.5, cy - 4.5, max(2.0, 23 * pct / 100), 9), 2, 2)
        if bolt:
            c = x + 14
            poly = QPainterPath()
            poly.moveTo(c + 1.5, cy - 6)
            poly.lineTo(c - 4, cy + 1)
            poly.lineTo(c - 0.5, cy + 1)
            poly.lineTo(c - 1.5, cy + 6)
            poly.lineTo(c + 4, cy - 1.5)
            poly.lineTo(c + 0.5, cy - 1.5)
            poly.closeSubpath()
            p.fillPath(poly, QColor(0, 0, 0, 210))

    def draw_battery(self, p, r):
        t = self.payload or {}
        cy = r.center().y()
        pct = t.get("pct", 0)
        low = t.get("label") == "Low battery"
        color = GREEN if t.get("charging") else (RED if low else self.c_text)
        self.draw_battery_icon(p, r.x() + 18, cy, pct, color, t.get("charging"))
        self.text(p, QRectF(r.x() + 62, r.y(), r.width() - 120, r.height()), t.get("label", ""), 11, True)
        self.text(p, QRectF(r.right() - 62, r.y(), 46, r.height()), f"{pct}%", 11, True,
                  color, Qt.AlignVCenter | Qt.AlignRight)

    def draw_speaker(self, p, x, cy, level, muted, color):
        body = QPainterPath()
        body.moveTo(x, cy - 3)
        body.lineTo(x + 4, cy - 3)
        body.lineTo(x + 9, cy - 7)
        body.lineTo(x + 9, cy + 7)
        body.lineTo(x + 4, cy + 3)
        body.lineTo(x, cy + 3)
        body.closeSubpath()
        p.fillPath(body, color)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        if muted:
            p.drawLine(int(x + 13), int(cy - 4), int(x + 19), int(cy + 4))
            p.drawLine(int(x + 19), int(cy - 4), int(x + 13), int(cy + 4))
        else:
            for i in range(1 if level < 34 else 2 if level < 67 else 3):
                rad = 4 + i * 4
                p.drawArc(QRectF(x + 9 - rad, cy - rad, 2 * rad, 2 * rad), -45 * 16, 90 * 16)

    def draw_sun(self, p, x, cy, color):
        c = QPointF(x + 9, cy)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 4, 4)
        p.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + 6.5 * math.cos(a), c.y() + 6.5 * math.sin(a)),
                       QPointF(c.x() + 9 * math.cos(a), c.y() + 9 * math.sin(a)))

    def draw_level(self, p, r, kind):
        t = self.payload or {}
        cy = r.center().y()
        vol, muted = t.get("vol", 0), t.get("mute", False)
        color = self.c_sub if muted else self.c_text
        if kind == "sun":
            self.draw_sun(p, r.x() + 18, cy, color)
        else:
            self.draw_speaker(p, r.x() + 18, cy, vol, muted, color)
        track = QRectF(r.x() + 52, cy - 3, r.width() - 52 - 62, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(self.c_dim)
        p.drawRoundedRect(track, 3, 3)
        if not muted and vol > 0:
            fill = QRectF(track.x(), track.y(), track.width() * min(vol, 100) / 100, track.height())
            p.setBrush(self.c_text if vol <= 100 else ORANGE)
            p.drawRoundedRect(fill, 3, 3)
        self.text(p, QRectF(r.right() - 58, r.y(), 44, r.height()), "Muted" if muted else f"{vol}%",
                  10.5, True, color, Qt.AlignVCenter | Qt.AlignRight)

    def draw_volume(self, p, r):
        self.draw_level(p, r, "speaker")

    def draw_brightness(self, p, r):
        self.draw_level(p, r, "sun")

    def draw_toast(self, p, r):
        t = self.payload or {}
        self.text(p, QRectF(r.x() + 18, r.y(), r.width() - 36, r.height()),
                  t.get("text", ""), 11, True, t.get("color") or self.c_text, Qt.AlignCenter)

    def draw_icon(self, p, kind, rect, color=None):
        if color is None:
            color = self.c_text
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        path = QPainterPath()
        if kind == "play":
            path.moveTo(x + w * 0.28, y + h * 0.15)
            path.lineTo(x + w * 0.28, y + h * 0.85)
            path.lineTo(x + w * 0.82, y + h * 0.5)
            path.closeSubpath()
        elif kind == "pause":
            path.addRoundedRect(QRectF(x + w * 0.22, y + h * 0.15, w * 0.2, h * 0.7), 2, 2)
            path.addRoundedRect(QRectF(x + w * 0.58, y + h * 0.15, w * 0.2, h * 0.7), 2, 2)
        else:                           # prev / next: two triangles + a bar
            d = 1 if kind == "next" else -1
            cx = x + w / 2
            for off in (-0.2, 0.2):
                a = cx + d * w * off
                path.moveTo(a - d * w * 0.2, y + h * 0.25)
                path.lineTo(a - d * w * 0.2, y + h * 0.75)
                path.lineTo(a + d * w * 0.2, y + h * 0.5)
                path.closeSubpath()
        p.fillPath(path, color)

    def kv(self, p, x, y, w, label, value, color=None):
        self.text(p, QRectF(x, y, w, 20), label, 9.5, False, self.c_sub)
        self.text(p, QRectF(x, y, w, 20), value, 10.5, True, color, Qt.AlignVCenter | Qt.AlignRight)

    def draw_dots(self, p, r):
        p.setPen(Qt.NoPen)
        for i, rect in enumerate(self.dot_rects(r)):
            p.setBrush(self.c_text if i == self.page else self.c_dim)
            p.drawEllipse(rect.center(), 2.5, 2.5)

    def draw_expanded(self, p, r):
        getattr(self, "page_" + PAGES[self.page])(p, r)
        self.draw_dots(p, r)
        chip = self.mode_chip(r)
        color = MODE_COLORS[self.vis_mode]
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 110), 1))
        p.drawRoundedRect(chip, 9, 9)
        self.text(p, chip, MODE_LABELS[self.vis_mode], 8.5, True, color, Qt.AlignCenter)
        tc = self.theme_chip(r)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self.c_dim, 1))
        p.drawRoundedRect(tc, 9, 9)
        self.text(p, tc, self.theme_label(), 8.5, True, self.c_sub, Qt.AlignCenter)

    def page_home(self, p, r):
        m = self.music
        if not m:
            now = datetime.now()
            self.text(p, QRectF(r.x(), r.y() + 14, r.width(), 64), now.strftime("%H:%M"),
                      40, False, self.c_text, Qt.AlignCenter)
            self.text(p, QRectF(r.x(), r.y() + 82, r.width(), 22), now.strftime("%A, %d %B"),
                      11, False, self.c_sub, Qt.AlignCenter)
            self.text(p, QRectF(r.x(), r.y() + 114, r.width(), 20), "Nothing playing",
                      9.5, False, self.c_dim, Qt.AlignCenter)
            return
        z = self.layout(r)
        self.draw_art(p, z["art"], 12)
        x = z["art"].right() + 14
        w = r.right() - 20 - x
        self.text(p, QRectF(x, r.y() + 24, w, 22), m["title"], 13, True)
        self.text(p, QRectF(x, r.y() + 48, w, 18), m["artist"] or m["album"], 10.5, False, self.c_sub)
        if m["status"] == "Playing":
            self.draw_eq(p, r.right() - 22, r.y() + 52, True)

        pos = self.music_position()
        bar = z["bar"]
        p.setPen(Qt.NoPen)
        p.setBrush(self.c_dim)
        p.drawRoundedRect(bar, 2.5, 2.5)
        if m["length"]:
            p.setBrush(self.accent)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * min(1, pos / m["length"]), bar.height()),
                              2.5, 2.5)
        self.text(p, QRectF(bar.x(), bar.bottom() + 3, 60, 14), fmt_time(pos), 8.5, False, self.c_sub)
        if m["length"]:
            self.text(p, QRectF(bar.right() - 60, bar.bottom() + 3, 60, 14), fmt_time(m["length"]),
                      8.5, False, self.c_sub, Qt.AlignVCenter | Qt.AlignRight)
        self.draw_icon(p, "prev", z["prev"])
        self.draw_icon(p, "pause" if m["status"] == "Playing" else "play", z["play"])
        self.draw_icon(p, "next", z["next"])

    def page_system(self, p, r):
        x, y = r.x() + 22, r.y()
        cpu = self.cpu_hist[-1]
        col = GREEN if cpu < 60 else ORANGE if cpu < 85 else RED
        self.text(p, QRectF(x, y + 16, 80, 16), "CPU", 9, False, self.c_sub)
        self.text(p, QRectF(x, y + 30, 150, 40), f"{cpu:.0f}%", 24, True, col)
        g = QRectF(x, y + 82, 170, 52)
        p.setPen(QPen(self.c_dim, 1))
        p.drawLine(QPointF(g.x(), g.bottom()), QPointF(g.right(), g.bottom()))
        hist = list(self.cpu_hist)
        path = QPainterPath()
        for i, v in enumerate(hist):
            px = g.x() + g.width() * i / (len(hist) - 1)
            py = g.bottom() - g.height() * min(v, 100) / 100
            if i == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col, 1.8))
        p.drawPath(path)

        cx = r.x() + 222
        w = r.right() - 22 - cx
        self.kv(p, cx, y + 14, w, "RAM", f"{self.ram:.0f}%", ORANGE if self.ram > 85 else self.c_text)
        bar = QRectF(cx, y + 38, w, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(self.c_dim)
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(ORANGE if self.ram > 85 else self.c_text)
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * min(self.ram, 100) / 100, 6), 3, 3)
        self.kv(p, cx, y + 58, w, "↓ Down", fmt_rate(self.net_rate[0]))
        self.kv(p, cx, y + 82, w, "↑ Up", fmt_rate(self.net_rate[1]))
        if self.temp is not None:
            self.kv(p, cx, y + 106, w, "Temp", f"{self.temp:.0f}°C",
                    RED if self.temp > 85 else ORANGE if self.temp > 70 else self.c_text)
        elif self.vpn:
            self.kv(p, cx, y + 106, w, "VPN", ", ".join(sorted(self.vpn)), GREEN)

    def page_focus(self, p, r):
        x, y = r.x(), r.y()
        remaining = max(0.0, self.timer_end - time.time()) if self.timer_end else 0
        ring = QRectF(x + 26, y + 24, 70, 70)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self.c_dim, 5))
        p.drawEllipse(ring)
        if self.timer_end:
            p.setPen(QPen(self.timer_color, 5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, 90 * 16, int(-(remaining / self.timer_total) * 360 * 16))
        self.text(p, ring, fmt_time(remaining + 0.99) if self.timer_end else fmt_time(POMO["focus"]),
                  12.5, True, self.c_text if self.timer_end else self.c_sub, Qt.AlignCenter)
        label = self.timer_label or ("Timer" if self.timer_end else "Ready")
        sub = f"Session {self.pomo_done + 1}" if self.pomo_active else "Pomodoro 25 / 5"
        self.text(p, QRectF(x + 108, y + 40, 90, 20), label, 12.5, True,
                  self.timer_color if self.timer_end else self.c_text)
        self.text(p, QRectF(x + 108, y + 62, 90, 18), sub, 9.5, False, self.c_sub)

        b = self.pomo_buttons(r)
        for key, text, color in (("btn", "Stop" if self.pomo_active else "Start",
                                  RED if self.pomo_active else GREEN),
                                 ("skip", "Skip", self.c_text)):
            if key == "skip" and not self.pomo_active:
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(self.c_btn)
            p.drawRoundedRect(b[key], 14, 14)
            self.text(p, b[key], text, 10.5, True, color, Qt.AlignCenter)

        p.setPen(QPen(self.c_dim, 1))
        p.drawLine(QPointF(x + 206, y + 22), QPointF(x + 206, y + 150))
        cx, w = x + 222, r.width() - 222 - 22
        self.text(p, QRectF(cx, y + 16, w, 14), "NEXT CTF", 8, True, self.c_sub)
        nxt = self.upcoming("ctf")
        if nxt:
            occ, ev = nxt
            self.text(p, QRectF(cx, y + 30, w, 20), str(ev.get("name", "")), 11.5, True)
            self.text(p, QRectF(cx, y + 50, w, 30), fmt_countdown((occ - datetime.now()).total_seconds()),
                      19, True, self.accent if self.music else GREEN)
            self.text(p, QRectF(cx, y + 82, w, 16), occ.strftime("%a %d %b · %H:%M"), 9, False, self.c_sub)
        else:
            self.text(p, QRectF(cx, y + 34, w, 20), "No CTF set", 11.5, True, self.c_sub)
            self.text(p, QRectF(cx, y + 56, w, 28), "island ctf NAME WHEN", 8.5, False, self.c_dim)
        self.text(p, QRectF(cx, y + 108, w, 14), "NEXT REMINDER", 8, True, self.c_sub)
        rem = self.upcoming("remind")
        if rem:
            occ, ev = rem
            when = occ.strftime("%H:%M") if occ.date() == datetime.now().date() else occ.strftime("%a %H:%M")
            self.text(p, QRectF(cx, y + 123, w - 50, 18), str(ev.get("name", "")), 10.5, True)
            self.text(p, QRectF(cx + w - 50, y + 123, 50, 18), when, 9.5, False, self.c_sub,
                      Qt.AlignVCenter | Qt.AlignRight)
        else:
            self.text(p, QRectF(cx, y + 123, w, 18), "None", 10, False, self.c_dim)

    def page_shelf(self, p, r):
        x, y = r.x(), r.y()
        sr = self.shelf_rects(r)
        msg, until = self.shelf_msg
        if msg and time.monotonic() < until:
            self.text(p, QRectF(x + 22, y + 10, sr["paste"].x() - x - 28, 18), msg, 9, True, self.accent)
        else:
            self.text(p, QRectF(x + 22, y + 10, 160, 18), f"SHELF  {len(self.shelf)}/{MAX_SHELF}",
                      8.5, True, self.c_sub)
        for key, label in (("paste", "Paste"), ("copy", "Copy all"), ("clear", "Clear")):
            p.setPen(Qt.NoPen)
            p.setBrush(self.c_btn)
            p.drawRoundedRect(sr[key], 9, 9)
            self.text(p, sr[key], label, 8.5, True,
                      self.c_text if (self.shelf or key == "paste") else self.c_dim, Qt.AlignCenter)

        area = QRectF(x + 16, y + 32, r.width() - 32, 118)
        p.setBrush(Qt.NoBrush)
        if self.dragging:
            p.setPen(QPen(self.accent, 1.6, Qt.DashLine))
            p.drawRoundedRect(area, 12, 12)
        elif not self.shelf:
            p.setPen(QPen(self.c_dim, 1.2, Qt.DashLine))
            p.drawRoundedRect(area, 12, 12)
        if not self.shelf:
            self.text(p, QRectF(area.x(), area.y() + 28, area.width(), 24),
                      "Release to add" if self.dragging else "Drop files or folders here",
                      12, True, self.accent if self.dragging else self.c_sub, Qt.AlignCenter)
            self.text(p, QRectF(area.x(), area.y() + 56, area.width(), 18),
                      f"Up to {MAX_SHELF} · or copy files, then press Paste", 8.5, False,
                      self.c_dim, Qt.AlignCenter)
            return
        for i, (rect, xr) in enumerate(self.shelf_rows(r)):
            it = self.shelf[i]
            if self.hover_pos is not None and rect.contains(self.hover_pos):
                p.setPen(Qt.NoPen)
                p.setBrush(self.c_btn)
                p.drawRoundedRect(rect, 8, 8)
            pm = self.icon_pixmap(it["path"])
            if pm:
                p.drawPixmap(QRectF(rect.x() + 6, rect.y() + 2.5, 16, 16).toRect(), pm)
            self.text(p, QRectF(rect.x() + 30, rect.y(), rect.width() - 30 - 90, rect.height()),
                      it["name"], 10)
            self.text(p, QRectF(rect.right() - 90, rect.y(), 62, rect.height()), it["info"], 8.5,
                      False, self.c_sub, Qt.AlignVCenter | Qt.AlignRight)
            self.text(p, xr, "×", 12, True, self.c_sub, Qt.AlignCenter)

    def icon_for(self, item):
        key = item.get("icon") or item.get("name", "")
        if key not in self.icon_cache:
            ic = QIcon.fromTheme(item["icon"]) if item.get("icon") else QIcon()
            self.icon_cache[key] = None if ic.isNull() else ic.pixmap(64, 64)
        return self.icon_cache[key]

    def page_launch(self, p, r):
        cells = self.launch_cells(r)
        if not cells:
            self.text(p, QRectF(r.x(), r.y() + 60, r.width(), 24), "No launchers in config.json",
                      10, False, self.c_sub, Qt.AlignCenter)
            return
        for rect, item in cells:
            ic = QRectF(rect.center().x() - 15, rect.y() + 4, 30, 30)
            pm = self.icon_for(item)
            if pm:
                p.drawPixmap(ic.toRect(), pm)
            else:
                hue = zlib.crc32(str(item.get("name", "?")).encode()) % 360
                p.setPen(Qt.NoPen)
                p.setBrush(QColor.fromHsl(hue, 150, 90))
                p.drawEllipse(ic)
                self.text(p, ic, (str(item.get("name", "?"))[:1] or "?").upper(), 12, True,
                          self.c_text, Qt.AlignCenter)
            self.text(p, QRectF(rect.x(), rect.y() + 38, rect.width(), 16), str(item.get("name", "")),
                      9, False, self.c_sub, Qt.AlignCenter)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)     # erase everything first, so old
        p.fillRect(self.rect(), Qt.transparent)                   # frames can never leave ghost trails
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.pill_rect()
        if r.height() < 1:
            p.end()
            return
        rad = min(r.height() / 2, self.corner)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        if self.theme_bg2 is not None:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, self.bg_cur)
            g.setColorAt(1, self.theme_bg2)
            p.fillPath(path, QBrush(g))
        else:
            p.fillPath(path, self.bg_cur)
        p.setPen(QPen(self.c_border, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setClipPath(path)
        p.setOpacity(self.alpha)
        draw = getattr(self, "draw_" + self.mode, None)
        if draw:
            draw(p, r)
        p.end()


# --------------------------------------------------------------------- main --
def main():
    app = QApplication(sys.argv)
    args = sys.argv[1:]
    if args:                            # client mode: talk to the running island
        if args[0] == "shelf" and len(args) > 2 and args[1] == "add":
            args[2:] = [os.path.abspath(os.path.expanduser(a)) for a in args[2:]]
        if send_command(" ".join(shlex.quote(a) for a in args)):
            sys.exit(0)
        print("Island is not running.")
        sys.exit(1)
    if send_command("ping"):
        print("Island is already running.")
        sys.exit(0)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    app.setQuitOnLastWindowClosed(False)
    island = Island()
    island.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
