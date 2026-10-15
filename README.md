# Focus Timer

An always-on-top desktop countdown timer with millisecond precision, built in Python with tkinter. It is designed to make starting (and finishing) a focused work session harder to put off.

## Features

- Stays above other windows; on macOS it also floats over full-screen apps and follows you across desktops
- Countdown with milliseconds (for example `24:59.874`)
- Color phases that build urgency: green, yellow, orange, then pulsing red
- Task box to name what you are working on before you start
- Presets (5, 10, 25, 45 minutes) and a custom time
- Daily scoreboard of sessions and focused minutes, saved between runs
- Works fully offline

## Run it

Requires Python 3.

```
python3 floating_timer.py
```

On macOS, for the "above full-screen apps / all desktops" behavior:

```
python3 -m pip install pyobjc-framework-Cocoa
```

Without it, the timer still works with standard always-on-top.

## How it works

The code is commented to explain the design decisions, including why the countdown measures against a fixed end time instead of subtracting each tick (to avoid clock drift), why it uses `after()` instead of a sleep loop (to keep the window responsive), and how the stats are persisted to a JSON file.

## Limitations and ideas

- macOS-only window pinning
- No packaged app yet (PyInstaller would allow double-click launch)
- Stats only track the current day

## Built with

Python, tkinter, and PyObjC. Developed with the help of an AI assistant (Claude), then tested and refined by me.
