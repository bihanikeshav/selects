"""PyInstaller entry point — invokes the travelcull CLI.

Kept as a tiny standalone module so PyInstaller has a concrete script to
analyze (console_scripts entry points aren't directly buildable).
"""
from travelcull.cli import main

if __name__ == "__main__":
    main()
