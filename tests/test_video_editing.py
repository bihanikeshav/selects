from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from selects.media.edits import EditRecipe, EditRecipeError, EditSegment
from selects.media.export import OutputProbe, build_ffmpeg_command, run_export
from selects.media.jobs import HeavyJobEngine, parse_progress_line
from selects.media.stabilize import analyze_stabilization, build_stabilization_preview_command, stabilization_filter


def test_recipe_round_trips_and_rejects_overlap() -> None:
    recipe = EditRecipe.from_dict(
        {
            "segments": [{"start_sec": 1, "end_sec": 3}, {"start_sec": 5, "end_sec": 8}],
            "gain_db": 3,
            "stabilize": {"enabled": True, "strength": "low", "crop": 0.04},
            "export": {"quality": "high", "container": "mp4"},
        }
    )
    assert EditRecipe.from_dict(recipe.to_dict()) == recipe
    recipe.validate(10)
    with pytest.raises(EditRecipeError):
        EditRecipe(segments=(EditSegment(0, 2), EditSegment(1, 3)))
    with pytest.raises(EditRecipeError):
        recipe.validate(7)


def test_fast_command_is_copy_only_and_precise_command_concats_segments(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "out.mp4"
    source.write_bytes(b"source")
    fast = build_ffmpeg_command(source, output, EditRecipe(segments=(EditSegment(2, 4),)), mode="fast")
    assert "-c" in fast and fast[fast.index("-c") + 1] == "copy"
    assert "-ss" in fast and "-to" in fast

    precise = build_ffmpeg_command(
        source,
        output,
        EditRecipe(segments=(EditSegment(0, 1), EditSegment(3, 5)), mute_audio=True),
        mode="precise",
        has_audio=False,
    )
    assert "-filter_complex" in precise
    assert "concat=n=2:v=1:a=0" in precise[precise.index("-filter_complex") + 1]
    assert "-c:v" in precise


def test_run_export_uses_atomic_temp_and_never_writes_source(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "exports" / "clip.mp4"
    original = b"original"
    source.write_bytes(original)

    def fake_runner(command, **_kwargs):
        Path(command[-1]).write_bytes(b"rendered")

    result = run_export(
        source,
        output,
        EditRecipe(segments=(EditSegment(0, 1),)),
        process_runner=fake_runner,
        probe_fn=lambda path: OutputProbe(1.0, path.stat().st_size),
    )
    assert result.output_path == output.resolve()
    assert output.read_bytes() == b"rendered"
    assert source.read_bytes() == original
    assert not list(output.parent.glob(".*.selects-*"))


def test_stabilization_analysis_and_fallback_filter_are_lightweight() -> None:
    still = np.zeros((16, 16, 3), dtype=np.uint8)
    moving = still.copy()
    moving[:, 8:] = 255
    analysis = analyze_stabilization(frames=[still, moving, still], threshold=0.1)
    assert analysis.sample_count == 3
    assert analysis.suggested is True
    assert "deshake" in stabilization_filter("medium", 0.05, prefer_vidstab=False)
    command = build_stabilization_preview_command("in.mp4", "preview.mp4")
    assert command[-1] == "preview.mp4"
    assert "-i" in command


def test_progress_parser_reads_microseconds() -> None:
    state: dict[str, str] = {}
    update = parse_progress_line("out_time_ms=2500000", duration_sec=10, state=state)
    assert update is not None
    assert update.out_time_sec == pytest.approx(2.5)
    assert update.percent == pytest.approx(25)


def test_job_engine_serializes_and_cancels_work() -> None:
    engine = HeavyJobEngine()
    entered: list[int] = []
    release = threading.Event()

    def first(context):
        entered.append(1)
        release.wait(2)
        context.check_cancelled()

    def second(context):
        entered.append(2)

    first_id = engine.submit(first, kind="export")
    second_id = engine.submit(second, kind="export")
    for _ in range(100):
        if engine.snapshot(first_id).state == "running":
            break
        time.sleep(0.01)
    assert engine.snapshot(first_id).state == "running"
    assert engine.cancel(first_id) is True
    release.set()
    engine.wait(first_id, timeout=3)
    engine.wait(second_id, timeout=3)
    assert engine.snapshot(first_id).state == "cancelled"
    assert engine.snapshot(second_id).state == "completed"
    assert entered == [1, 2]
    engine.shutdown()
