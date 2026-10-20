"""Selects commands for local editorial review."""
import json
from dataclasses import asdict
from pathlib import Path

import click

from selects.config import FolderConfig
from selects.export import preview_xmp_writes, rating_sidecar_path, write_xmp_ratings

from .candidates import generate_candidates
from .catalog import load_photos
from .reviews import effective_reviews, import_reviews, report, verify_sources
from .schema import CreativeReview
from .selection import shortlist
from .storage import check_state, write_json


class ReviewCommands(click.Group):
    def invoke(self, context):
        try:
            return super().invoke(context)
        except (OSError, ValueError) as error:
            raise click.ClickException(str(error)) from error


def output(value):
    click.echo(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


library = click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))


@click.group(cls=ReviewCommands)
def creative():
    """Prepare, import and export photo reviews."""


@creative.command("schema")
def schema_command():
    """Describe the JSON fields accepted for one review."""
    output(CreativeReview.model_json_schema())


@creative.command("status")
@library
def status_command(folder):
    """Show analysis and review coverage."""
    cfg = FolderConfig(folder=folder)
    check_state(cfg)
    if not cfg.db_path.is_file():
        output(dict(library=str(cfg.folder), indexed=False,
                    next_passes=["index", "classical", "embed", "aesthetic", "moment", "story"]))
        return
    catalog = load_photos(cfg)
    reviews, warnings = effective_reviews(cfg, catalog)
    status = dict(library=str(cfg.folder), indexed=True, photos=len(catalog),
                  reviewed=len(reviews), warnings=warnings)
    for name, field, predicate in (
        ("classical_pending", "classical_done", lambda v: not v),
        ("embedding_pending", "embedding_done", lambda v: not v),
        ("aesthetic_missing", "aesthetic", lambda v: v is None),
        ("hyperiqa_missing", "hyperiqa", lambda v: v is None),
        ("preview_missing", "preview_path", lambda v: v is None),
        ("photos_in_moments", "moment_id", lambda v: v is not None),
        ("photos_in_stories", "story_ids", bool),
    ):
        status[name] = sum(predicate(p[field]) for p in catalog.values())
    output(status)


@creative.command("candidates")
@library
@click.option("--percentile", default=50., type=click.FloatRange(0, 100), show_default=True)
@click.option("--max-candidates", type=click.IntRange(min=1))
@click.option("--include-story/--no-include-story", default=True)
@click.option("--include-maybe", is_flag=True, help="Include the entire eligible pool.")
@click.option("--batch-size", default=50, type=click.IntRange(30, 80), show_default=True)
@click.option("--batch", type=click.IntRange(min=1))
@click.option("--moment", "moment_id", type=click.IntRange(min=1))
@click.option("--story", "story_id", type=click.IntRange(min=1))
def candidates_command(folder, batch, **options):
    """Save review batches from indexed metadata."""
    cfg = FolderConfig(folder=folder)
    manifest = generate_candidates(cfg, **options)
    packet = None
    if batch is not None:
        if batch > len(manifest["batches"]):
            raise ValueError(f"batch {batch} is unavailable")
        packet = manifest["batches"][batch - 1]
    suffix = "".join(f"_{kind}_{options[key]}" for kind, key in
                     (("moment", "moment_id"), ("story", "story_id")) if options[key] is not None)
    path = write_json(cfg, f"candidates{suffix}.json", manifest)
    click.echo(f"Manifest saved to {path}", err=True)
    if packet is not None:
        included = set(packet["photo_ids"])
        manifest = dict(manifest, batches=[packet],
                        candidates=[p for p in manifest["candidates"] if p["photo_id"] in included])
    output(manifest)


@creative.command("import")
@library
@click.argument("results", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--replace", is_flag=True)
def import_command(folder, results, replace):
    """Validate and save reviewer judgments."""
    output(import_reviews(FolderConfig(folder=folder), results, replace=replace))


@creative.command("report")
@library
def report_command(folder):
    """Write the current selection in JSON, CSV and Markdown."""
    output(report(FolderConfig(folder=folder)))


@creative.command("shortlist")
@library
@click.option("--heroes", default=12, type=click.IntRange(min=0), show_default=True)
@click.option("--grades", default=40, type=click.IntRange(min=0), show_default=True)
@click.option("--story", default=80, type=click.IntRange(min=0), show_default=True)
def shortlist_command(folder, **caps):
    """Apply nested highlight, grading and story caps."""
    cfg = FolderConfig(folder=folder)
    result = shortlist(cfg, **caps)
    result["report"] = report(cfg)
    output(result)


@creative.command("xmp")
@library
@click.option("--apply/--dry-run", default=False, help="Preview by default; apply writes sidecars.")
@click.option("--force", is_flag=True)
def xmp_command(folder, apply, force):
    """Export review decisions as separate rating sidecars."""
    cfg = FolderConfig(folder=folder)
    catalog = load_photos(cfg)
    judgments, warnings = effective_reviews(cfg, catalog)
    if apply and warnings:
        raise ValueError("shortlist is stale; refresh it before applying sidecars")
    targets = set()
    triples = []
    for review in judgments:
        pid = review["photo_id"]
        path = Path(catalog[pid]["path"])
        target = rating_sidecar_path(path).resolve()
        if target in targets:
            raise ValueError(f"sidecar collision: {target}")
        targets.add(target)
        triples.append((pid, path, review["decision"]))
    if apply:
        verify_sources(cfg, judgments, catalog)
    operation = write_xmp_ratings if apply else preview_xmp_writes
    plans = operation(triples, force=force, library_root=cfg.folder, sidecar_only=True)
    output(dict(dry_run=not apply, warnings=warnings, plans=[asdict(p) for p in plans]))
    if apply and any(p.action == "no_op" for p in plans):
        raise click.ClickException("some sidecars failed; inspect the exported plan")
