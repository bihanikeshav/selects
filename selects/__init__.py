"""selects – local AI-assisted travel photo and video culling."""

import os

# Model weights come from the HF Hub. Its Xet downloader can hang mid-file
# (seen on macOS with huggingface_hub 1.x: a partial file stuck at 0 bytes while
# holding the download lock, which also blocks every stage waiting on that
# model). Plain HTTP downloads are reliable, so make them the default.
# huggingface_hub reads this once at import, so it has to be set here, before
# anything imports it. Set HF_HUB_DISABLE_XET=0 to opt back in.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

__version__ = "0.1.15"
