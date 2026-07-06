from __future__ import annotations

from pathlib import Path

import click

from selects.config import get_folder_config
from selects.db import init_db
from selects.gpu import detect_capabilities
from selects.indexer.orchestrator import index_folder
from selects.pipeline import run_classical_stage


@click.group()
def main():
    """selects — local AI-assisted travel photo & video culling."""


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--pass", "pass_",
    type=click.Choice(["all", "index", "classical", "embed", "tag", "smart_tag", "ram_tag",
                       "thematic", "date", "story", "face_embed", "moment"]),
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
        from selects.ml.embed import run_embedding_stage
        n = run_embedding_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] embed: {name}", err=True),
        )
        click.echo(f"embed: {n} processed")

    if pass_ == "tag":
        from selects.ml.tags import run_tag_stage
        n = run_tag_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] tag: {name}", err=True),
        )
        click.echo(f"tag: {n} photos tagged")

    if pass_ == "smart_tag":
        from selects.ml.smart_clusters import run_smart_cluster_stage
        n = run_smart_cluster_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] smart_tag: {name}", err=True),
        )
        click.echo(f"smart_tag: {n} photos clustered")

    if pass_ == "thematic":
        from selects.ml.thematic_clusters import run_thematic_stage
        n = run_thematic_stage(cfg, on_progress=None)
        click.echo(f"thematic: {n} location clusters")

    if pass_ == "date":
        from selects.ml.thematic_clusters import run_date_stage
        n = run_date_stage(cfg, on_progress=None)
        click.echo(f"date: {n} day clusters")

    if pass_ == "ram_tag":
        from selects.ml.ram_tags import run_ram_tagging_stage
        n = run_ram_tagging_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] ram_tag: {name}", err=True),
        )
        click.echo(f"ram_tag: {n} photos tagged")

    if pass_ in ("all", "story"):
        from selects.ml.stories import run_story_stage
        n = run_story_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] story: {name}", err=True),
        )
        click.echo(f"story: {n} stories built")

    if pass_ == "face_embed":
        from selects.ml.faces import run_face_embedding_stage
        n = run_face_embedding_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] face_embed: {name}", err=True),
        )
        click.echo(f"face_embed: {n} photos processed")

    if pass_ == "moment":
        from selects.ml.moments import run_moment_stage
        n = run_moment_stage(
            cfg,
            on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] moment: {name}", err=True),
        )
        click.echo(f"moment: {n} moments built")


@main.command()
@click.argument(
    "folder",
    required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--port", default=8000, type=int)
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
    """Report CUDA / NVDEC / nvImageCodec / cv2.cuda capabilities."""
    caps = detect_capabilities()
    click.echo(f"CUDA              : {'yes' if caps.cuda_available else 'no'} ({caps.device_name})")
    click.echo(f"CUDA capability   : {caps.cuda_capability}")
    click.echo(f"VRAM              : {caps.vram_total_mb} MB")
    click.echo(f"NVDEC (torchcodec): {'yes' if caps.nvdec_available else 'no'}")
    click.echo(f"nvImageCodec      : {'yes' if caps.nvimgcodec_available else 'no'}")
    click.echo(f"cv2.cuda          : {'yes' if caps.cv2_cuda_available else 'no'}")
