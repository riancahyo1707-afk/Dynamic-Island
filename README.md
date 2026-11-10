 DYNAMIC ISLAND for KDE / Linux
 An iPhone-style "Dynamic Island" that lives at the top-center of your screen.
 Python + PySide6. Tested design target: KDE Plasma on Kali Linux (Wayland/X11).
==============================================================================

CONTENTS
  1.  Files in this folder
  2.  Install / update / remove
  3.  What it does
  4.  Mouse and buttons
  5.  Terminal commands  (the "island" command)
  6.  Visibility modes   (Automatic / Standby / Popup / Closed)
  7.  Pages in the expanded view
  8.  File shelf
  9.  Themes and custom colors
 10.  CTF events, reminders, Pomodoro
 11.  Config file reference
 12.  Troubleshooting
 13.  Known limitations


------------------------------------------------------------------------------
1. FILES IN THIS FOLDER
------------------------------------------------------------------------------
  island.py    the program
  install.sh   installer (sets up everything, see section 2)
  README.txt   this file


------------------------------------------------------------------------------
2. INSTALL / UPDATE / REMOVE
------------------------------------------------------------------------------
INSTALL (first time)
  Put island.py and install.sh in the same folder (for example Downloads):

      cd ~/Downloads
      bash install.sh

  The installer will:
    - install packages with apt (playerctl, libxcb-cursor0, python3-venv,
      pulseaudio-utils, x11-xserver-utils, network-manager, iproute2,
      dbus-bin). It asks for your sudo password once.
    - create a Python venv and install PySide6
    - copy the program to        ~/.local/share/dynamic-island/
    - create the "island" command ~/.local/bin/island
    - create a systemd user service (starts at every login, restarts on crash)
    - add "Add to Island Shelf" to the Dolphin right-click menu

  If the "island" command is not found afterwards, log out and in once so
  ~/.local/bin is in your PATH.

UPDATE (new island.py)
  Copying the file into Downloads does NOT update the running program. Do:

      cp ~/Downloads/island.py ~/.local/share/dynamic-island/island.py
      systemctl --user restart dynamic-island

  (or run "bash install.sh" again, which does the same and more).

SERVICE CONTROL
      systemctl --user status  dynamic-island
      systemctl --user restart dynamic-island
      systemctl --user stop    dynamic-island
      journalctl --user -u dynamic-island -f        (live log)

REMOVE
      systemctl --user disable --now dynamic-island
      rm -rf ~/.local/share/dynamic-island ~/.local/bin/island
      rm -f  ~/.config/systemd/user/dynamic-island.service
      rm -f  ~/.local/share/kio/servicemenus/island-shelf.desktop
      rm -rf ~/.config/dynamic-island               (your settings)


------------------------------------------------------------------------------
3. WHAT IT DOES
------------------------------------------------------------------------------
  Music          Album art, title/artist, progress bar (click to seek),
                 prev / play-pause / next. Reads any MPRIS player through
                 playerctl (Spotify, browsers, VLC...). A small equalizer
                 shows in the collapsed pill; track changes pop up briefly.
  Notifications  Pops down for notifications from any app (via D-Bus).
                 Click it to dismiss.
  Volume         Overlay with bar, percentage and mute (PulseAudio/PipeWire).
  Brightness     Overlay for screen brightness (laptops with a backlight).
  Battery        Plug / unplug pop-ups and low-battery warning (20% and 10%).
  Caps/Num Lock  On/off pop-up.
  VPN            "VPN connected - 10.10.14.5" when tun0 / wg0 / tap0 / ppp0 /
                 tailscale0 appears, red message when it drops, and a small
                 green "VPN" badge while idle. (Handy for HackTheBox/THM.)
  Wi-Fi / LAN    Pop-up when the network connects or disconnects.
  Timer          Progress ring + countdown in the collapsed pill.
  Pomodoro       25 min focus / 5 min break, long break (15) after 4 sessions.
  Clock          Big clock and date when nothing is playing.
  System page    CPU graph, RAM, download/upload speed, temperature.
  CTF + reminders  Countdown to your next CTF, alerts before it starts.
  Launcher       App buttons (Terminal, Files, Browser, Settings, or your own).
  File shelf     Temporary holder for up to 5 files/folders.
  Themes         Black, glass, clear, light, midnight, hacker, sunset, album,
                 plus your own colors and opacity.
  Modes          Automatic / Standby / Popup / Closed.


------------------------------------------------------------------------------
4. MOUSE AND BUTTONS
------------------------------------------------------------------------------
  Left click          expand / collapse (click a notification to dismiss it)
  Middle click        play / pause
  Right click         menu: mode, theme, timers, pomodoro, clear shelf, quit
  Mouse wheel         switch pages while expanded
  Dots (bottom)       click a dot to jump to that page
  Bottom-right chip   shows the current MODE; click = next mode
  Bottom-left chip    shows the current THEME; click = next theme
  Tray icon           left click = next mode, right click = full menu
                      (needed to bring the island back in Popup/Closed mode)

  The expanded view closes by itself about 2.5 s after the mouse leaves it
  (6 s when opened by a terminal command and the mouse is elsewhere).


------------------------------------------------------------------------------
5. TERMINAL COMMANDS
------------------------------------------------------------------------------
  The "island" command talks to the running island.

  island timer 5m              start a timer (90 = seconds, 5m, 1h, 25m ...)
  island cancel                cancel the timer / pomodoro
  island toast "Scan done"     show a message
  island toggle                expand / collapse
  island page NAME             open expanded view on: home shelf system focus launch
  island pomodoro              start (or stop if running)
  island pomodoro stop | skip
  island ctf "NAME" "WHEN"     add a CTF event
  island remind "NAME" "WHEN" [daily|weekly]
  island forget "NAME"         delete events with that name
  island mode auto|standby|popup|closed|cycle
  island theme NAME | next
  island style KEY VALUE       tweak the current theme (section 9)
  island style reset
  island shelf                 open the shelf page
  island shelf add FILE...     add files/folders to the shelf
  island shelf paste           add files you copied (Ctrl+C) in a file manager
  island shelf copy            copy everything on the shelf to the clipboard
  island shelf clear
  island reload                re-read config.json
  island quit                  stop the island (the service restarts it only
                               on crash; use systemctl to stop it for good)

  WHEN formats:  "2026-11-20 19:00"  |  "19:00" (next time it happens)
                 |  "+30m" (30 minutes from now)

  Tip: put "island toast" after long commands:
      nmap -sV target -oN scan.txt; island toast "Scan finished"

  Tip: bind a key in System Settings > Shortcuts > Add Command, for example
  "island mode cycle" or "island shelf paste".


------------------------------------------------------------------------------
6. VISIBILITY MODES
------------------------------------------------------------------------------
  Standby    Always visible as an idle pill. Shows all pop-ups. (default)
  Popup      Hidden until something happens, then pops out and hides again.
  Closed     Fully hidden. Nothing shows, including alerts (do-not-disturb).
  Automatic  Standby while music is PLAYING or a timer/pomodoro is running or
             the island is expanded; otherwise Popup. During "quiet_hours"
             (config) it is Closed.

  Switch with the bottom-right chip, right-click menu, tray icon, or
  "island mode ...". The choice is saved and survives restarts.
  Quiet hours example (config.json):   "quiet_hours": ["23:00", "07:00"]

  In Popup/Closed the window is really hidden: you cannot click it. Use the
  tray icon or "island mode standby" to bring it back. In Closed mode,
  "island toggle" and "island page" are ignored.


------------------------------------------------------------------------------
7. PAGES IN THE EXPANDED VIEW
------------------------------------------------------------------------------
  Home    music player, or clock + date when nothing is playing
  Shelf   temporary file shelf (section 8)
  System  CPU graph, RAM bar, down/up speed, temperature (or VPN interface)
  Focus   pomodoro ring + Start/Stop/Skip, next CTF countdown, next reminder
  Launch  up to 8 app buttons (edit "launchers" in config.json)


------------------------------------------------------------------------------
8. FILE SHELF
------------------------------------------------------------------------------
  Holds up to 5 files or folders, ONLY in memory: everything is gone when the
  island quits or restarts. It stores references, not copies - if you move or
  delete the original, the item disappears from the shelf.

  ADD
    - Right-click files in Dolphin > "Add to Island Shelf"   (works everywhere)
    - Copy files in Dolphin (Ctrl+C), then click "Paste" on the Shelf page,
      or run: island shelf paste
    - island shelf add FILE...
    - Drag and drop onto the island (see Known limitations: this does NOT work
      on KDE Wayland)
  USE
    - Click an item or "Copy all", then press Ctrl+V in any file manager
    - Drag an item out (Shift+drag = all items) - same Wayland limitation
    - Click the x on a row to remove one, "Clear" to remove all
  A small number badge shows in the idle pill while files are on the shelf.
  Optional auto-expiry: "shelf_expire_minutes": 120  (0 = never).


------------------------------------------------------------------------------
9. THEMES AND CUSTOM COLORS
------------------------------------------------------------------------------
  Built-in themes:
    black      solid black (default)
    glass      dark, about 60% see-through
    clear      very transparent with a visible edge
    light      off-white with dark text
    midnight   navy-to-purple gradient, blue accents
    hacker     black with green monospace text
    sunset     purple-to-orange gradient
    album      background tinted with the album art color

  Change:  island theme glass      (or click the bottom-left chip, or use the
                                    right-click / tray menu)

  Tweak the current theme (switching theme clears the tweaks):
    island style opacity 0.35        0.05 - 1
    island style bg "#102030"        background color
    island style bg2 "#401030"       second color = gradient
    island style text "#ffd479"      text color
    island style accent "#ff5577"    progress bars, equalizer
    island style art_accent false    true = accent follows album art
    island style border "#ffffff"
    island style border_opacity 0.2
    island style corner 20           corner roundness
    island style font "Fira Code"
    island style tint 0.4            mix album color into the background
    island style reset

  Your own theme: add it to config.json, then "island theme mine":
    "themes": { "mine": { "label": "Mine", "bg": "#202020", "opacity": 0.5,
                          "text": "#ffd479", "accent": "#ff5577",
                          "bg2": "#401030" } }

  Very transparent themes can be hard to read on bright wallpapers; "glass"
  with opacity 0.6-0.7 is a good balance. Background blur is not available.


------------------------------------------------------------------------------
10. CTF EVENTS, REMINDERS, POMODORO
------------------------------------------------------------------------------
  island ctf "HTB Cyber Apocalypse" "2026-11-20 19:00"
  island remind "Network Security class" "08:00" daily

  Alerts:   CTF       1 day before, 1 hour, 10 minutes, and at the start
            Reminder  10 minutes before and at the start
  The Focus page shows the countdown to the next CTF and the next reminder.
  An alert that was due while the island was off is not shown later (only
  alerts due within about 2 minutes are shown). Closed mode skips alerts.

  Pomodoro: Focus page > Start, or "island pomodoro". 25/5, long break (15)
  after every 4th focus session. The collapsed pill shows the ring + time.


------------------------------------------------------------------------------
11. CONFIG FILE REFERENCE
------------------------------------------------------------------------------
  Location: ~/.config/dynamic-island/config.json  (created on first run).
  After editing, run:  island reload      (theme/mode changes also apply via
  their commands). Restart the service after changing the two window options.

  "mode"                 "auto" | "standby" | "popup" | "closed"
  "quiet_hours"          [] or ["23:00", "07:00"]  (Automatic mode -> Closed)
  "theme"                theme name
  "themes"               your own themes (see section 9)
  "style"                tweaks set by "island style"
  "events"               CTF events / reminders (edited by the commands)
  "launchers"            buttons on the Launch page, up to 8:
                         { "name": "Burp", "cmd": "burpsuite",
                           "icon": "burpsuite" }
  "shelf_expire_minutes" 0 = never, otherwise auto-remove after N minutes
  "x11_bypass_wm"        true (default) | false. The island runs through
                         XWayland as a window that bypasses KDE's window
                         rules. Set false only if you want to test whether KDE
                         then treats it differently (position/stacking may
                         change). Needs a service restart.
  "input_mask"           true (default) | false. true = clicks pass through the
                         transparent area around the pill. Set false only if
                         you still see ghost rectangles; then the invisible
                         470x210 area at the top-center catches clicks.
                         Needs a service restart.


------------------------------------------------------------------------------
12. TROUBLESHOOTING
------------------------------------------------------------------------------
  Nothing appears after install
    - systemctl --user status dynamic-island
    - journalctl --user -u dynamic-island -n 30
    - Run it by hand to see the error:
        ~/.local/share/dynamic-island/venv/bin/python \
        ~/.local/share/dynamic-island/island.py
    - If the log mentions "xcb" / "cursor": sudo apt install libxcb-cursor0
    - Check the mode chip / tray icon: Popup or Closed hide the island.

  My new island.py changes nothing
    The service runs ~/.local/share/dynamic-island/island.py, not the file in
    Downloads. Copy it there and restart (section 2).

  Ghost rectangles / stripes / trails after expanding or collapsing
    Fixed in the latest island.py (the window is cleared on every repaint).
    If you still see them, set "input_mask": false and restart.

  Ctrl+C does not stop it when started by hand
    Use Ctrl+\ , or:  pkill -f island.py

  No notifications
    dbus-monitor is needed:  which dbus-monitor   (else: sudo apt install dbus-bin)

  No volume / music pop-ups
    pactl and playerctl are needed:  which pactl playerctl

  Caps Lock / Num Lock pop-up never shows
    It reads the keyboard LEDs from /sys/class/leds, with "xset q" as backup,
    which may not work on KDE Wayland.

  Brightness pop-up never shows
    Only works when /sys/class/backlight exists (laptops).

  Wi-Fi name missing in the pop-up
    Needs nmcli (network-manager).

  Drag and drop onto the island does nothing
    Known limitation on KDE Wayland (section 13). Use the Dolphin right-click
    entry or Paste.

  Debug drag and drop
      systemctl --user stop dynamic-island
      ISLAND_DEBUG=1 ~/.local/bin/island
    Drag a file over the pill. "[island] dragEnter ..." printing means the
    island receives it; nothing printing means KDE does not deliver the drag.
    Finish with Ctrl+C and: systemctl --user start dynamic-island


------------------------------------------------------------------------------
13. KNOWN LIMITATIONS
------------------------------------------------------------------------------
  - Wayland: the island runs through XWayland so it can position itself. On
    KDE Wayland that means drag and drop from native apps (Dolphin) onto or out
    of the island does not work. Use "Add to Island Shelf", Paste and
    Copy all + Ctrl+V instead. A fully native Wayland version would need a
    rewrite with GTK4 + gtk4-layer-shell.
  - It may appear on top of fullscreen apps. Automatic mode cannot detect
    fullscreen windows.
  - playerctl follows the first media player it finds. To pin one, edit the
    "metadata" call in poll_music() to add:  "-p", "spotify".
  - The island shows on the primary monitor only.
  - The shelf is memory-only by design (see section 8).
  - Written and tested offscreen; real-desktop behavior (tray icon, drag and
    drop, Caps Lock, brightness, Wi-Fi detection) depends on your KDE setup.
