from __future__ import annotations

import os
from pathlib import Path

import click

from selects.config import get_folder_config
from selects.db import init_db
from selects.gpu import detect_capabilities
from selects.server.pipeline_runner import STAGE_FUNCS, get_stage_callable, run_pipeline_stages

_PASS_CHOICES = ("all", *STAGE_FUNCS)


def _default_web_port() -> int:
    raw = os.environ.get("SELECTS_WEB_PORT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 8000


@click.group()
def main():
    """selects — local AI-assisted travel photo & video culling."""


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--pass", "pass_",
    type=click.Choice(_PASS_CHOICES, case_sensitive=True),
    default="all",
)
def index(folder: Path, pass_: str):
    """Index a folder and run available pipeline stages."""
    cfg = get_folder_config(folder)
    init_db(cfg.db_path)

    if pass_ == "all":
        def publish(msg: dict) -> None:
            text = msg.get("message") or msg.get("stage") or ""
            if text:
                click.echo(text, err=True)

        run_pipeline_stages(cfg, publish)
        return

    fn = get_stage_callable(pass_)
    n = fn(
        cfg,
        on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] {pass_}: {name}", err=True),
    )
    click.echo(f"{pass_}: {n}")


@main.command()
@click.argument(
    "folder",
    required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--port", default=_default_web_port, show_default=8000, type=int)
@click.option("--host", default="127.0.0.1", type=str)
@click.option("--no-browser", is_flag=True)
@click.option("--no-background", is_flag=True, help="Don't auto-run indexer on startup")
def serve(folder: Path | None, port: int, host: str, no_browser: bool, no_background: bool):
    """Serve the web UI + API.

    With FOLDER: serve that folder as the (bootstrap) library. Without FOLDER:
    serve the registry's active library if one exists, otherwise start with no
    active library so the web onboarding page can create the first one.
    """
    import threading

    import uvicorn

    from selects.logging_setup import setup_logging
    setup_logging()

    from selects.server.app import build_app
    from selects.server.library_manager import LibraryManager

    if folder is not None:
        cfg = get_folder_config(folder)
        init_db(cfg.db_path)
        app = build_app(cfg, run_background=not no_background)
    else:
        manager = LibraryManager()
        _libs, active_id = manager.list_libraries()
        if active_id is not None:
            try:
                manager.activate(active_id)
            except Exception:
                pass
        app = build_app(manager=manager, run_background=not no_background)

    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    if not no_browser:
        from selects.launcher import open_ui

        # open_ui polls until the server answers, then opens an app window —
        # more reliable than a fixed sleep + webbrowser.open when double-clicked.
        threading.Thread(target=open_ui, args=(url,), daemon=True).start()

    click.echo(f"selects serving at {url}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@main.command()
def doctor():
    """Report the active ONNX Runtime provider / nvImageCodec / cv2.cuda."""
    caps = detect_capabilities()
    click.echo(f"GPU acceleration  : {'yes' if caps.gpu_available else 'no'}")
    click.echo(f"ONNX provider     : {caps.provider} ({caps.device_name})")
    if caps.installed_providers:
        click.echo(f"Installed EPs     : {', '.join(caps.installed_providers)}")
    click.echo(f"VRAM              : {caps.vram_total_mb} MB")
    click.echo(f"nvImageCodec      : {'yes' if caps.nvimgcodec_available else 'no'}")
    click.echo(f"cv2.cuda          : {'yes' if caps.cv2_cuda_available else 'no'}")
