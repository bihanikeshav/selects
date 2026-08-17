#!/bin/sh
# selects one-line installer (macOS / Linux).
#
#   curl -LsSf https://bihanikeshav.github.io/selects/install.sh | sh
#
# Installs through uv, so there is no downloaded app bundle for Gatekeeper to
# flag and nothing to code-sign. uv brings its own managed Python, so you do
# not need Python installed first. Re-running upgrades selects in place.
set -eu

say() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }

# 1. Ensure uv. Piping the official installer through sh is not quarantined the
#    way a browser download is, so this stays warning-free.
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python toolchain)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# uv and the tools it installs land in ~/.local/bin (or ~/.cargo/bin on older
# uv). Put those on PATH for the launch below even if the shell was started
# before uv existed.
for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
  case ":$PATH:" in
    *":$d:"*) ;;
    *) [ -d "$d" ] && PATH="$d:$PATH" ;;
  esac
done
export PATH

# 2. Install selects with the on-device AI extra (torch, onnxruntime,
#    insightface, ...). --force makes a re-run upgrade an existing install.
say "Installing selects (pulls the AI stack; first launch also downloads models)..."
uv tool install --python 3.11 --force "selects[ml]"

# 3. Launch. The AI model weights (~3.5 GB) download from inside the app's
#    first-run setup screen, with a progress bar - not here.
say "Starting selects..."
exec selects serve
