"""
Focus Timer: an always-on-top desktop countdown with millisecond precision.

Features
- Floats above other windows (and, on macOS, above full-screen apps and
  across all desktops).
- Countdown with milliseconds, e.g. 24:59.874.
- Color phases (green -> yellow -> orange -> pulsing red) to build urgency.
- Task box: name what you are working on before you start.
- Daily scoreboard (sessions and minutes), saved between runs.

Run:
    python3 floating_timer.py

Optional (macOS): for the "above full-screen apps / all desktops" behavior,
install PyObjC first:
    python3 -m pip install pyobjc-framework-Cocoa

Libraries
    tkinter   built-in GUI toolkit (window, buttons, labels)
    time      perf_counter() gives a precise clock for the countdown
    json      saves and loads the daily stats
    os        builds the path to the stats file
    datetime  today's date, so stats reset each day
    AppKit    (optional, macOS) native window control through PyObjC

Design notes are written as short "Why ...?" questions with answers next to
the code they explain.
"""

import tkinter as tk
import time
import json
import os
import datetime

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
# Constants are kept at the top so behavior can be changed in one place.
PRESETS = [5, 10, 25, 45]  # preset buttons, in minutes

# Why is the stats file in the home folder?
# Keeping it outside the project folder means it can never be committed to
# Git by accident. The leading dot makes it a hidden file.
STATS_FILE = os.path.expanduser("~/.floating_timer_stats.json")


# ---------------------------------------------------------------------------
# Saving and loading stats
# ---------------------------------------------------------------------------
# How does the app remember data after it closes?
# It writes a small JSON file to disk. JSON is plain text that maps directly
# onto Python dictionaries. This is called persistence.

def load_stats():
    """Read today's stats from disk, or start fresh if there are none."""
    today = str(datetime.date.today())
    try:
        with open(STATS_FILE) as f:
            s = json.load(f)
        # Only reuse saved numbers from today, so the scoreboard resets daily.
        if s.get("date") == today:
            return s
    except Exception:
        # What if the file is missing or corrupted?
        # The error is caught and the app falls through to fresh zeros instead
        # of crashing. (A missing file is normal on the first run.)
        pass
    return {"date": today, "sessions": 0, "minutes": 0}


def save_stats(s):
    """Write the stats dictionary to disk as JSON."""
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(s, f)
    except Exception:
        # If saving fails (disk full, permissions), the timer keeps working.
        # Losing a stat is better than crashing mid-session.
        pass


# ---------------------------------------------------------------------------
# Color phases
# ---------------------------------------------------------------------------
def phase(remaining, total):
    """Return (background, text color, message) for the time left.

    This is a pure function: it only depends on its inputs and changes
    nothing outside itself, which makes it easy to reason about and to test.
    For example, phase(30, 1500) always returns the red "last stretch" values.
    """
    # Fraction of the session left: 1.0 = just started, 0.0 = finished.
    # "if total else 0" avoids dividing by zero.
    frac = remaining / total if total else 0

    # Checks run top to bottom and stop at the first match, so they are
    # ordered from most urgent to least urgent.
    if remaining <= 60 or frac < 0.10:
        return "#b00020", "#ffffff", "LAST STRETCH. GO GO GO."
    if frac < 0.25:
        return "#e65100", "#ffffff", "Final stretch. Finish strong."
    if frac < 0.50:
        return "#f9a825", "#1a1a1a", "Past halfway. Do NOT stop now."
    return "#1b5e20", "#ffffff", "Locked in. Just keep going."
    # Colors are hex codes: #RRGGBB (two hex digits each for red, green, blue).


# ---------------------------------------------------------------------------
# macOS: float above everything
# ---------------------------------------------------------------------------
_pin_reported = False  # so a failure message prints only once


def pin_on_top_mac():
    """Ask macOS to keep the window above everything, including full-screen
    apps, and to show it on every desktop (Space).

    Why is this needed when tkinter already has -topmost?
    On macOS, -topmost is weak: full-screen apps and other desktops can still
    cover the window. This sets the window's level directly through AppKit
    (via PyObjC). A higher level is drawn above lower levels.

    Why is it optional?
    It only works on a Mac with PyObjC installed. If either is missing, the
    timer still runs with normal always-on-top. This is graceful degradation:
    a platform-specific extra that never breaks the core app.
    """
    global _pin_reported
    try:
        # Imported here so the file still runs on systems without PyObjC.
        from AppKit import NSApplication
        app = NSApplication.sharedApplication()
        # Activation policy 1 = "accessory": no Dock icon, and allowed to
        # overlay full-screen apps.
        app.setActivationPolicy_(1)
        for w in app.windows():
            w.setLevel_(25)  # status-window level, above normal windows
            # Collection behavior is a set of flags combined with | (bitwise OR):
            #   1   show on all desktops (Spaces)
            #   16  stay in place when switching desktops
            #   256 allowed to appear over full-screen apps
            w.setCollectionBehavior_(1 | 16 | 256)
            w.setHidesOnDeactivate_(False)  # stay visible when another app is active
    except Exception as e:
        # Report a failure once instead of hiding it, so problems are visible.
        if not _pin_reported:
            print(f"[pin_on_top_mac] not applied: {e!r}")
            _pin_reported = True


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
class FloatingTimer:
    """The timer window and its logic.

    Why a class instead of plain functions?
    The timer has state: data that changes over time (is it running? how many
    seconds are left?). A class bundles that state with the methods that
    change it. Every method receives "self", which is how they share the same
    variables. Without a class this would need global variables, which are
    harder to track and debug.
    """

    def __init__(self, root):
        """Build the window. Runs once at startup."""
        self.root = root
        root.title("Focus")
        root.attributes("-topmost", True)  # basic always-on-top, any OS
        root.resizable(False, False)       # fixed size keeps the layout simple
        root.geometry("+40+40")            # start 40px from the left and top

        # --- State ---------------------------------------------------------
        # What is the "state" of this app?
        # These variables. Buttons do not draw anything themselves; they only
        # change this state, and the display is updated to match. Most bugs in
        # apps like this come from the state and the display disagreeing
        # (for example, a button says PAUSE while running is False), so the
        # state is kept in one place.
        self.stats = load_stats()       # today's sessions and minutes
        self.total = 25 * 60.0          # session length, in seconds
        self.remaining = self.total     # seconds left (float, for milliseconds)
        self.end_time = None            # clock time when the countdown hits zero
        self.running = False            # True while counting down
        self.job = None                 # handle of the scheduled tick, so it can be cancelled

        # --- Layout --------------------------------------------------------
        # tkinter builds windows from widgets (Label, Button, Entry, Frame).
        # A Frame is an invisible box that groups widgets; .pack() places each
        # widget below the previous one.

        # Colored display area. Only this part changes color with the phases.
        self.top = tk.Frame(root, bg="#263238")
        self.top.pack(fill="x")

        self.task_label = tk.Label(self.top, text="What are you working on?",
                                   font=("Helvetica", 14, "bold"),
                                   bg="#263238", fg="white", wraplength=300)
        self.task_label.pack(padx=14, pady=(12, 0))

        # Why a monospace font (Menlo)?
        # Every character has the same width, so the digits do not jitter left
        # and right while they change many times per second.
        self.clock = tk.Label(self.top, text=self.fmt(self.remaining),
                              font=("Menlo", 46, "bold"), bg="#263238", fg="white")
        self.clock.pack(padx=14, pady=4)

        self.msg = tk.Label(self.top, text="Pick a time, name the task, hit Start.",
                            font=("Helvetica", 13), bg="#263238", fg="white")
        self.msg.pack(padx=14, pady=(0, 12))

        # Task entry. Naming one specific task before starting makes it harder
        # to drift (a technique known as an "implementation intention").
        row = tk.Frame(root)
        row.pack(pady=(8, 2))
        tk.Label(row, text="Task:").pack(side="left")
        # StringVar is a tkinter variable linked to the Entry box; what the
        # user typed is read with self.task_var.get().
        self.task_var = tk.StringVar()
        tk.Entry(row, textvariable=self.task_var, width=24).pack(side="left", padx=4)

        # Preset buttons, created in a loop from the PRESETS list.
        presets = tk.Frame(root)
        presets.pack(pady=4)
        for m in PRESETS:
            # Why "m=m" in the lambda?
            # A lambda looks up its variables when it is called (on click), not
            # when it is created. Without "m=m", every button would use the
            # last value of m (45). Writing m=m freezes the current value into
            # each button.
            tk.Button(presets, text=f"{m}m", width=3,
                      command=lambda m=m: self.set_minutes(m)).pack(side="left", padx=2)
        self.custom = tk.Entry(presets, width=4, justify="center")
        self.custom.pack(side="left", padx=(8, 2))
        tk.Button(presets, text="Set", command=self.set_custom).pack(side="left")

        # Start/Pause and Reset buttons.
        controls = tk.Frame(root)
        controls.pack(pady=4)
        self.start_btn = tk.Button(controls, text="START", width=8, command=self.toggle)
        self.start_btn.pack(side="left", padx=3)
        tk.Button(controls, text="Reset", width=8, command=self.reset).pack(side="left", padx=3)

        self.stats_label = tk.Label(root, text="", font=("Helvetica", 11))
        self.stats_label.pack(pady=(2, 10))
        self.update_stats_label()

        # Start the "keep on top" loop shortly after the window exists.
        root.after(300, self.keep_on_top)

    def keep_on_top(self):
        """Re-assert always-on-top every 2 seconds.

        Another app can push the window back at any time, so the app keeps
        stating where it wants to be. This is polling: checking repeatedly
        instead of waiting for an event.
        """
        self.root.attributes("-topmost", True)
        self.root.lift()
        pin_on_top_mac()
        self.root.after(2000, self.keep_on_top)  # schedule itself again

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def fmt(seconds):
        """Turn seconds like 1499.874 into the text '24:59.874'.

        @staticmethod: this function does not need "self"; it only uses its
        input, so it is not tied to a particular timer.
        """
        seconds = max(seconds, 0)                          # never show negative time
        ms = int(round((seconds - int(seconds)) * 1000))   # fractional part, in ms
        whole = int(seconds)
        # Rounding can produce 1000 ms, which is not a valid display
        # ("00:05.1000"), so carry it into the next whole second.
        if ms == 1000:
            ms, whole = 0, whole + 1
        m, s = divmod(whole, 60)  # divmod returns (minutes, leftover seconds)
        # :02d pads to 2 digits with zeros; :03d pads to 3.
        return f"{m:02d}:{s:02d}.{ms:03d}"

    def paint(self, bg, fg):
        """Set the background (bg) and text (fg) colors of the display area."""
        for w in (self.top, self.task_label, self.clock, self.msg):
            w.config(bg=bg)
        for w in (self.task_label, self.clock, self.msg):
            w.config(fg=fg)

    def update_stats_label(self):
        self.stats_label.config(
            text=f"Today: {self.stats['sessions']} sessions done, "
                 f"{self.stats['minutes']} min focused")

    # -----------------------------------------------------------------------
    # Actions (what the buttons do)
    # -----------------------------------------------------------------------
    def set_minutes(self, m):
        """Choose a new session length (presets and the custom box)."""
        self.stop()  # always stop first so two countdowns can never run at once
        self.total = self.remaining = float(m) * 60
        self.clock.config(text=self.fmt(self.remaining))
        self.paint("#263238", "white")
        self.msg.config(text="Name the task, then hit Start.")

    def set_custom(self):
        """Read the custom-minutes box and apply it if valid.

        What if the user types "abc" or "-5"?
        This is input validation. float("abc") raises a ValueError, which is
        caught and ignored so the app does not crash, and the "m > 0" check
        rejects zero and negative numbers. User input is never trusted.
        """
        try:
            m = float(self.custom.get())
            if m > 0:
                self.set_minutes(m)
        except ValueError:
            pass

    def toggle(self):
        """The Start / Pause / Resume button.

        What happens if Start is clicked twice quickly?
        Nothing harmful. This is one toggle that checks the "running" state
        first: if already running, the second click pauses instead of starting
        another countdown. The state variable acts as a guard. (Two countdowns
        at once would make the clock run twice as fast.)
        """
        if self.running:
            # Pause
            self.stop()
            self.msg.config(text="Paused. Don't wander off. Come back!")
            self.start_btn.config(text="RESUME")
            return

        # Start or resume
        if self.remaining <= 0:
            self.remaining = self.total  # finished earlier, so start over
        task = self.task_var.get().strip()  # strip() removes stray spaces
        self.task_label.config(text=f"NOW: {task}" if task else "NOW: Focus")
        self.running = True
        # Convert "seconds left" into a fixed finish time on the clock
        # (see the explanation in tick()).
        self.end_time = time.perf_counter() + self.remaining
        self.start_btn.config(text="PAUSE")
        self.tick()

    def stop(self):
        """Stop counting and cancel any scheduled tick so it cannot fire later."""
        self.running = False
        self.start_btn.config(text="START")
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None

    def reset(self):
        """Return to the full session length with a clean display."""
        self.stop()
        self.remaining = self.total
        self.clock.config(text=self.fmt(self.remaining))
        self.paint("#263238", "white")
        self.task_label.config(text="What are you working on?")
        self.msg.config(text="Pick a time, name the task, hit Start.")

    # -----------------------------------------------------------------------
    # The countdown
    # -----------------------------------------------------------------------
    def tick(self):
        """Runs about every 30 ms while the timer is running.

        Why not a loop with time.sleep(0.03)?
        A GUI is driven by an event loop (root.mainloop() at the bottom of the
        file) that constantly handles clicks, typing, and redraws. A loop with
        sleep() would block it and the window would freeze. Instead,
        root.after(30, self.tick) says "call tick again in 30 ms" and hands
        control back, so the event loop stays free in between.

        Why store an end time instead of subtracting on each tick?
        Because of drift. after(30) does not fire at exactly 30 ms; it may take
        31, 35, or 50 depending on how busy the computer is. Doing
        "remaining -= 0.03" each tick would lose every delay and the clock
        would run slow. Saving the real finish time once
        (end_time = now + remaining) and measuring the distance to it on every
        tick means late ticks do not accumulate.

        Why perf_counter() and not time.time()?
        perf_counter() is a high-resolution monotonic clock: it only moves
        forward and ignores changes to the system clock (daylight saving, time
        syncs). time.time() can jump, which would break a countdown.
        """
        if not self.running:
            return  # safety check: do nothing if stopped

        now = time.perf_counter()
        self.remaining = self.end_time - now  # accurate time left

        if self.remaining <= 0:
            # Time is up.
            self.remaining = 0
            self.stop()
            self.clock.config(text=self.fmt(0))
            self.finish()
            return

        # Pick the colors and message for how much time is left.
        bg, fg, text = phase(self.remaining, self.total)
        # In the last stretch, alternate between two reds about 3 times a
        # second so the display pulses. int(now * 3) % 2 gives 0,1,0,1,...
        if bg == "#b00020" and int(now * 3) % 2 == 0:
            bg, fg = "#ff1744", "#ffffff"
        self.paint(bg, fg)
        self.msg.config(text=text)
        self.clock.config(text=self.fmt(self.remaining))

        # Schedule the next tick; keep its ID so stop() can cancel it.
        self.job = self.root.after(30, self.tick)

    def finish(self):
        """Called once when a session completes. Updates and saves stats."""
        self.stats["sessions"] += 1
        self.stats["minutes"] += int(round(self.total / 60))
        save_stats(self.stats)
        self.update_stats_label()
        self.msg.config(text="DONE! You did it. Take a real break.")
        self.root.lift()  # bring the window forward so it is noticed
        self.flash(10)

    def flash(self, n):
        """Flash green and beep, counting down from n.

        This is recursion driven by after(): the method schedules itself with
        n - 1 and stops when n reaches 0 (the base case). As with tick(),
        after() is used instead of a loop with sleep() so the window never
        freezes.
        """
        if n == 0:
            self.paint("#1b5e20", "white")
            return
        self.root.bell()
        self.paint("#00c853" if n % 2 == 0 else "#1b5e20", "white")
        self.root.after(350, lambda: self.flash(n - 1))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# What does `if __name__ == "__main__"` do?
# When a file is run directly, Python sets __name__ to "__main__", so this
# block runs. If another file imports this one, __name__ is the module name
# instead and the app does not launch by accident. It is the standard way to
# say "run this only when executed as a script."
if __name__ == "__main__":
    root = tk.Tk()         # create the main window
    FloatingTimer(root)    # build the timer inside it
    root.mainloop()        # start the event loop; runs until the window closes


# ---------------------------------------------------------------------------
# Known limitations and future work
# ---------------------------------------------------------------------------
#  * Requires Python. Could be packaged as a standalone macOS app with
#    PyInstaller so it opens with a double-click.
#  * tkinter's look is basic and button colors are limited on macOS. A
#    toolkit like PyQt would allow a more polished design.
#  * The always-on-top behavior is macOS-only (PyObjC). Windows and Linux
#    would need their own versions of pin_on_top_mac().
#  * If Start is pressed while the finish flash is still running, the two
#    animations briefly compete for the colors. Fix: cancel the flash when a
#    new session starts.
#  * No automated tests yet. phase() and fmt() are pure functions and easy to
#    unit test, e.g. assert fmt(65.5) == "01:05.500".
#  * Stats only hold today's totals. A per-day history and streak counter
#    would be a natural next step.
