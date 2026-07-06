"""PyInstaller entry point — invokes the selects CLI.

Kept as a tiny standalone module so PyInstaller has a concrete script to
analyze (console_scripts entry points aren't directly buildable).
"""
import sys

if __name__ == "__main__":
    # No arguments = double-clicked the app: open the native desktop window.
    # With arguments, behave as the normal CLI (serve/index/doctor/…).
    if len(sys.argv) == 1:
        from selects.desktop import run_app

        run_app()
    else:
        from selects.cli import main

        main()
