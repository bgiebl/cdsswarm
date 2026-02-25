# VHS tape file for recording the cdsswarm TUI demo
# Usage: pip install -e . && vhs examples/demo.tape
# Requires: vhs, ttyd, ffmpeg, and cdsswarm installed

Output img/demo.gif

Set Width 960
Set Height 480
Set FontSize 16
Set Padding 10
Set Framerate 5

# Hide the initial command typing and TUI startup
Hide
Type "python examples/demo_textual.py --workers 4 --tasks 10 --seed 42"
Enter
Sleep 3s
Show

# Workers tab: watch status transitions and download progress
Sleep 8s

# Switch to Files tab
Type "t"
Sleep 5s

# Switch back to Workers tab
Type "t"
Sleep 5s

# Open log screen for selected worker
Enter
Sleep 4s

# Dismiss log screen
Escape
Sleep 3s

# Quit
Type "q"
