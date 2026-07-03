from __future__ import annotations

import webbrowser
from pathlib import Path

import click

from travelcull.config import get_folder_config
from travelcull.db import init_db
from travelcull.gpu import detect_capabilities
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage


@click.group()
def main():
    """travelcull — local AI-assisted travel photo & video culling."""


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--pass", "pass_",
    type=click.Choice(["all", "index", "classical", "embed", "tag", "story"]),
    default="all",
)
def index(folder: Path, pass_: str):
    """Index a folder and run available pipeline stages."""
    cfg = get_folder_config(folder)
    init_db(cfg.db_path)

    if pass_ in ("all", "index"):
        added = index_folder(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] {name}", err=True),
        )
        click.echo(f"indexed: {added} new files")

    if pass_ in ("all", "classical"):
        processed = run_classical_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] classical: {name}", err=True),
        )
        click.echo(f"classical: {processed} processed")

    if pass_ == "embed":
        from travelcull.ml.embed import run_embedding_stage
        n = run_embedding_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] embed: {name}", err=True),
        )
        click.echo(f"embed: {n} processed")

    if pass_ == "tag":
        from travelcull.ml.tags import run_tag_stage
        n = run_tag_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] tag: {name}", err=True),
        )
        click.echo(f"tag: {n} photos tagged")

    if pass_ in ("all", "story"):
        from travelcull.ml.stories import run_story_stage
        n = run_story_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] story: {name}", err=True),
        )
        click.echo(f"story: {n} stories built")


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--port", default=8000, type=int)
@click.option("--host", default="127.0.0.1", type=str)
@click.option("--no-browser", is_flag=True)
@click.option("--no-background", is_flag=True, help="Don't auto-run indexer on startup")
def serve(folder: Path, port: int, host: str, no_browser: bool, no_background: bool):
    """Serve the web UI for an indexed folder."""
    import uvicorn

    cfg = get_folder_config(folder)
    init_db(cfg.db_path)

    from travelcull.server.app import build_app

    app = build_app(cfg, run_background=not no_background)
    url = f"http://{host}:{port}"
    if not no_browser:
        webbrowser.open(url)
    click.echo(f"travelcull serving at {url}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@main.command()
def doctor():
    """Report CUDA / NVDEC / nvImageCodec / cv2.cuda capabilities."""
    caps = detect_capabilities()
    click.echo(f"CUDA              : {'yes' if caps.cuda_available else 'no'} ({caps.device_name})")
    click.echo(f"CUDA capability   : {caps.cuda_capability}")
    click.echo(f"VRAM              : {caps.vram_total_mb} MB")
    click.echo(f"NVDEC (torchcodec): {'yes' if caps.nvdec_available else 'no'}")
    click.echo(f"nvImageCodec      : {'yes' if caps.nvimgcodec_available else 'no'}")
    click.echo(f"cv2.cuda          : {'yes' if caps.cv2_cuda_available else 'no'}")
