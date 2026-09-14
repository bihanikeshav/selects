"""The HF Xet downloader is off by default (see selects/__init__.py).

huggingface_hub reads HF_HUB_DISABLE_XET once, at import time, so these run in
a fresh interpreter rather than against this process's already-imported copy.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _run(code: str, **env_overrides: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_DISABLE_XET"}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def test_importing_selects_disables_xet_by_default():
    assert _run("import os, selects; print(os.environ['HF_HUB_DISABLE_XET'])") == "1"


def test_user_setting_wins():
    code = "import os, selects; print(os.environ['HF_HUB_DISABLE_XET'])"
    assert _run(code, HF_HUB_DISABLE_XET="0") == "0"


def test_huggingface_hub_sees_the_default():
    pytest.importorskip("huggingface_hub")
    code = "import selects, huggingface_hub.constants as c; print(c.HF_HUB_DISABLE_XET)"
    assert _run(code) == "True"
