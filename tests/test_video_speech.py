"""Silence, filler, and topic spans from timed words."""
from __future__ import annotations

import numpy as np
import pytest

from selects.ml.video_speech import (
    Word,
    filler_spans,
    silence_spans,
    tokens_to_words,
    topic_spans,
    transcribe_pcm,
    transcribe_video,
)


def test_landscape_without_words_has_no_silence():
    assert silence_spans([], [(0.0, 40.0)], [0.0]) == []


def test_word_gap_in_speech_scene_is_silence():
    words = [Word(0.0, 0.3, "hello"), Word(2.0, 0.3, "there")]
    spans = silence_spans(words, [(0.0, 10.0)], [0.4])
    assert len(spans) == 1
    assert spans[0][1] - spans[0][0] >= 0.80


def test_like_is_not_filler_um_is():
    words = [Word(0.0, 0.2, "like"), Word(1.0, 0.2, "um"), Word(2.0, 0.4, "mountains")]
    spans = filler_spans(words)
    assert len(spans) == 1
    assert spans[0][0] <= 1.0 <= spans[0][1]


def test_you_know_phrase_is_filler():
    words = [Word(0.5, 0.2, "you"), Word(0.8, 0.25, "know")]
    spans = filler_spans(words)
    assert len(spans) == 1


def test_topic_merge_on_similar_embeddings():
    phrases = [(0.0, 5.0, "lake at dawn"), (5.0, 10.0, "the water at sunrise")]
    same = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    merged = topic_spans(phrases, [same, same])
    assert merged == [(0.0, 10.0, "lake at dawn")]
    other = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    split = topic_spans(phrases, [same, other])
    assert len(split) == 2


def test_transcribe_without_model_returns_empty(tmp_path):
    clip = tmp_path / "none.mp4"
    clip.write_bytes(b"not a video")
    assert transcribe_video(clip) == []


def test_log_mel_is_whisper_shaped():
    from selects.ml.video_speech import log_mel_spectrogram

    pcm = np.zeros(16000, dtype=np.float32)
    pcm[100:400] = 0.2
    mel = log_mel_spectrogram(pcm)
    assert mel.shape == (80, 3000)
    assert np.isfinite(mel).all()


def test_tokens_to_words_uses_timestamp_tokens():
    vocab = {100: "Ġhello", 101: "Ġworld"}
    words = tokens_to_words(
        [50364, 100, 101, 50364 + 50],
        offset=10.0,
        id_to_token=vocab,
    )
    assert [w.w for w in words] == ["hello", "world"]
    assert words[0].t == pytest.approx(10.0)
    assert words[-1].t + words[-1].d == pytest.approx(11.0)


def test_transcribe_pcm_offsets_each_window():
    pcm = np.zeros(16000 * 45, dtype=np.float32)

    def fake(_chunk: np.ndarray, offset: float) -> list[Word]:
        return [Word(offset, 0.2, "hi")]

    words = transcribe_pcm(pcm, decode_window=fake, window_sec=30.0)
    assert [round(w.t, 1) for w in words] == [0.0, 30.0]


def test_transcribe_pcm_stops_between_windows_on_cancel():
    pcm = np.zeros(16000 * 90, dtype=np.float32)
    calls = {"n": 0}

    def fake(_chunk: np.ndarray, offset: float) -> list[Word]:
        calls["n"] += 1
        return [Word(offset, 0.1, "x")]

    cancelled = {"v": False}

    def cancel() -> bool:
        return cancelled["v"]

    def wrapping(chunk: np.ndarray, offset: float) -> list[Word]:
        words = fake(chunk, offset)
        cancelled["v"] = True
        return words

    words = transcribe_pcm(pcm, decode_window=wrapping, window_sec=30.0, cancel=cancel)
    assert len(words) == 1
    assert calls["n"] == 1
