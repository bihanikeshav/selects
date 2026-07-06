"""PyInstaller entry point — invokes the selects CLI.

Kept as a tiny standalone module so PyInstaller has a concrete script to
analyze (console_scripts entry points aren't directly buildable).
"""
from selects.cli import main

if __name__ == "__main__":
    main()
