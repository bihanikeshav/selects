"""PyInstaller entry point — invokes the selects CLI.

Kept as a tiny standalone module so PyInstaller has a concrete script to
analyze (console_scripts entry points aren't directly buildable).
"""
import sys

from selects.cli import main

if __name__ == "__main__":
    # Double-clicking the packaged app launches it with no arguments; default to
    # `serve` so the server starts and the browser opens instead of just printing
    # help and exiting.
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    main()
