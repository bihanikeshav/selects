"""Speech selects: silence, filler, topics, and optional local Whisper."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

SILENCE_WORD_GAP_SEC = 0.80
FILLER_PAD_SEC = 0.08
SPEECH_SCENE_RATIO = 0.15
TOPIC_TEXT_COSINE = 0.80
WHISPER_WINDOW_SEC = 30.0
WHISPER_ASSET_ID = "whisper_small"
WHISPER_HF_REPO = "Systran/faster-whisper-small"

FILLER_WORDS = frozenset({"um", "uh", "er", "ah", "hmm"})
FILLER_PHRASES = (("you", "know"), ("i", "mean"))
_TOKEN_RE = re.compile(r"[a-z0-9']+")


@dataclass
class Word:
    t: float
    d: float
    w: str


def _norm_token(text: str) -> str:
    match = _TOKEN_RE.search(text.lower().strip())
    return match.group(0) if match else ""


def silence_spans(
    words: list[Word],
    scenes: list[tuple[float, float]],
    speech_ratio_by_scene: list[float] | None = None,
) -> list[tuple[float, float]]:
    """Gaps >= 0.80s only in speech-bearing scenes."""
    if not scenes:
        return []
    ratios = speech_ratio_by_scene or []
    speech_scenes: list[tuple[float, float]] = []
    for index, scene in enumerate(scenes):
        ratio = ratios[index] if index < len(ratios) else 0.0
        covered = any(scene[0] <= w.t < scene[1] for w in words)
        if covered or ratio >= SPEECH_SCENE_RATIO:
            speech_scenes.append(scene)
    if not speech_scenes or not words:
        return []
    ordered = sorted(words, key=lambda item: item.t)
    spans: list[tuple[float, float]] = []
    for left, right in zip(ordered, ordered[1:]):
        gap_start = left.t + left.d
        gap_end = right.t
        if gap_end - gap_start < SILENCE_WORD_GAP_SEC:
            continue
        if not any(scene[0] <= gap_start < scene[1] or scene[0] < gap_end <= scene[1] for scene in speech_scenes):
            continue
        # Clip the gap to the speech scene that contains the start.
        for scene in speech_scenes:
            if scene[0] <= gap_start < scene[1]:
                spans.append((gap_start, min(gap_end, scene[1])))
                break
    return spans


def filler_spans(words: list[Word]) -> list[tuple[float, float]]:
    """um/uh/... and short phrases; do not match bare 'like'."""
    if not words:
        return []
    tokens = [(_norm_token(word.w), word) for word in sorted(words, key=lambda item: item.t)]
    spans: list[tuple[float, float]] = []
    index = 0
    while index < len(tokens):
        token, word = tokens[index]
        matched = False
        for phrase in FILLER_PHRASES:
            if index + len(phrase) <= len(tokens) and tuple(t[0] for t in tokens[index:index + len(phrase)]) == phrase:
                start = word.t - FILLER_PAD_SEC
                last = tokens[index + len(phrase) - 1][1]
                end = last.t + last.d + FILLER_PAD_SEC
                spans.append((max(0.0, start), end))
                index += len(phrase)
                matched = True
                break
        if matched:
            continue
        if token in FILLER_WORDS:
            spans.append((max(0.0, word.t - FILLER_PAD_SEC), word.t + word.d + FILLER_PAD_SEC))
        index += 1
    return spans


def topic_spans(
    phrases: list[tuple[float, float, str]],
    embeddings: list[np.ndarray],
) -> list[tuple[float, float, str]]:
    """Merge adjacent windows with cosine >= 0.80. Label = first phrase."""
    if not phrases:
        return []
    if len(phrases) != len(embeddings):
        raise ValueError("phrases and embeddings must be the same length")
    merged: list[tuple[float, float, str]] = []
    start, end, label = phrases[0]
    prev = np.asarray(embeddings[0], dtype=np.float32).reshape(-1)
    prev = prev / (np.linalg.norm(prev) + 1e-12)
    for (p_start, p_end, p_label), raw in zip(phrases[1:], embeddings[1:]):
        vec = np.asarray(raw, dtype=np.float32).reshape(-1)
        vec = vec / (np.linalg.norm(vec) + 1e-12)
        cosine = float(np.dot(prev, vec))
        if cosine >= TOPIC_TEXT_COSINE:
            end = p_end
            prev = vec
            continue
        merged.append((start, end, label))
        start, end, label = p_start, p_end, p_label
        prev = vec
    merged.append((start, end, label))
    return merged


def words_to_phrases(words: list[Word], gap: float = 0.60) -> list[tuple[float, float, str, list[Word]]]:
    """Group words into phrases at pauses."""
    if not words:
        return []
    ordered = sorted(words, key=lambda item: item.t)
    groups: list[list[Word]] = [[ordered[0]]]
    for word in ordered[1:]:
        prev = groups[-1][-1]
        if word.t - (prev.t + prev.d) >= gap:
            groups.append([word])
        else:
            groups[-1].append(word)
    phrases = []
    for group in groups:
        text = " ".join(item.w for item in group)
        start = group[0].t
        end = group[-1].t + group[-1].d
        phrases.append((start, end, text, group))
    return phrases


WHISPER_SAMPLE_RATE = 16000
WHISPER_N_FFT = 400
WHISPER_HOP = 160
WHISPER_N_MELS = 80
WHISPER_N_SAMPLES = WHISPER_SAMPLE_RATE * 30
WHISPER_SOT = 50258
WHISPER_EOS = 50257
WHISPER_TRANSCRIBE = 50359
WHISPER_NO_TIMESTAMPS = 50363
WHISPER_TIMESTAMP_BEGIN = 50364
WHISPER_LANG_BEGIN = 50259
WHISPER_LANG_END = 50357
WHISPER_MAX_TOKENS = 448

_WHISPER_MODEL = None
_WHISPER_VOCAB: dict[int, str] | None = None
_WHISPER_FAILED = False


def tokens_to_words(
    ids: list[int],
    *,
    offset: float = 0.0,
    id_to_token: dict[int, str] | None = None,
    timestamp_begin: int = WHISPER_TIMESTAMP_BEGIN,
) -> list[Word]:
    """Split timestamped Whisper token ids into words."""
    vocab = id_to_token if id_to_token is not None else _whisper_vocab()
    special = {
        WHISPER_SOT,
        WHISPER_EOS,
        WHISPER_TRANSCRIBE,
        WHISPER_NO_TIMESTAMPS,
        50358,
        50360,
        50361,
        50362,
    }
    segments: list[tuple[float, float, list[int]]] = []
    start: float | None = None
    buf: list[int] = []
    for tid in ids:
        if tid >= timestamp_begin:
            stamp = (tid - timestamp_begin) * 0.02
            if start is None:
                start = stamp
                buf = []
            else:
                segments.append((start, stamp, buf))
                start = stamp
                buf = []
            continue
        if tid in special or WHISPER_LANG_BEGIN <= tid <= WHISPER_LANG_END:
            continue
        buf.append(tid)
    if start is None and buf:
        segments.append((0.0, max(0.02 * len(buf), 0.4), buf))
    words: list[Word] = []
    for seg_start, seg_end, token_ids in segments:
        text = _decode_tokens(token_ids, vocab).strip()
        pieces = [part for part in text.split() if part]
        if not pieces:
            continue
        duration = max(seg_end - seg_start, 0.02 * len(pieces))
        step = duration / len(pieces)
        for index, piece in enumerate(pieces):
            words.append(Word(offset + seg_start + index * step, step, piece))
    return words


def transcribe_pcm(
    pcm: np.ndarray,
    sample_rate: int = WHISPER_SAMPLE_RATE,
    *,
    offset: float = 0.0,
    cancel: Callable[[], bool] | None = None,
    window_sec: float = WHISPER_WINDOW_SEC,
    decode_window: Callable[[np.ndarray, float], list[Word]] | None = None,
) -> list[Word]:
    """Transcribe float32 PCM in ``window_sec`` chunks. Cancel between windows."""
    if pcm.size == 0:
        return []
    audio = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if sample_rate != WHISPER_SAMPLE_RATE and audio.size:
        # Linear resample is enough for the analysis windowing; ONNX still
        # sees 16 kHz because ffmpeg already converts in transcribe_video.
        duration = audio.size / float(sample_rate)
        target = max(1, int(round(duration * WHISPER_SAMPLE_RATE)))
        x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, target, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)
    if decode_window is not None:
        window = int(window_sec * WHISPER_SAMPLE_RATE)
        words: list[Word] = []
        start = 0
        while start < audio.size:
            if cancel is not None and cancel():
                break
            chunk = audio[start:start + window]
            if chunk.size < WHISPER_SAMPLE_RATE // 4:
                break
            stamp = offset + start / float(WHISPER_SAMPLE_RATE)
            try:
                words.extend(decode_window(chunk, stamp))
            except Exception as exc:
                log.warning("whisper window at %.1fs failed: %s", stamp, exc)
            start += window
            if cancel is not None and cancel():
                break
        return words
    if cancel is not None and cancel():
        return []
    return _transcribe_faster_whisper(audio, offset=offset)


def transcribe_video(
    path: Path,
    *,
    cancel: Callable[[], bool] | None = None,
    window_sec: float = WHISPER_WINDOW_SEC,
) -> list[Word]:
    """Return word timings. Empty if ffmpeg or faster-whisper weights are missing."""
    pcm = _read_pcm16(path, cancel=cancel)
    if pcm is None or pcm.size == 0:
        return []
    return transcribe_pcm(pcm, cancel=cancel, window_sec=window_sec)


def whisper_cache_dir() -> Path:
    from selects.ml.model_assets import asset_dir

    path = asset_dir("whisper_small")
    path.mkdir(parents=True, exist_ok=True)
    return path


def whisper_files_present() -> bool:
    folder = whisper_cache_dir()
    return (folder / "model.bin").is_file() and (folder / "config.json").is_file()


def _read_pcm16(path: Path, *, cancel: Callable[[], bool] | None = None) -> np.ndarray | None:
    try:
        from selects.media.runtime import resolve_ffmpeg, spawn_ffmpeg
    except Exception:
        return None
    runtime = resolve_ffmpeg()
    if runtime is None or not getattr(runtime, "ffmpeg", None):
        return None
    import subprocess

    process = spawn_ffmpeg(
        [
            "-hide_banner", "-loglevel", "error",
            "-i", os.fspath(path),
            "-vn", "-ac", "1", "-ar", str(WHISPER_SAMPLE_RATE),
            "-f", "s16le", "pipe:1",
        ],
        runtime=runtime,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    chunks: list[bytes] = []
    try:
        while True:
            if cancel is not None and cancel():
                process.terminate()
                return None
            data = process.stdout.read(64 * 1024)
            if not data:
                break
            chunks.append(data)
        process.wait(timeout=15)
    except Exception:
        process.kill()
        return None
    raw = b"".join(chunks)
    if len(raw) < 2:
        return None
    samples = np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype="<i2")
    return (samples.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


def _mel_filters() -> np.ndarray:
    n_fft = WHISPER_N_FFT
    n_mels = WHISPER_N_MELS
    sr = WHISPER_SAMPLE_RATE

    def hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
        return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)

    def mel_to_hz(mel: np.ndarray) -> np.ndarray:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    mels = np.linspace(hz_to_mel(0.0), hz_to_mel(sr / 2.0), n_mels + 2)
    hz = mel_to_hz(mels)
    bins = np.floor((n_fft + 1) * hz / sr).astype(int)
    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for i in range(n_mels):
        left, center, right = bins[i], bins[i + 1], bins[i + 2]
        if center == left:
            center += 1
        if right == center:
            right += 1
        for j in range(left, center):
            if 0 <= j < filters.shape[1]:
                filters[i, j] = (j - left) / (center - left)
        for j in range(center, right):
            if 0 <= j < filters.shape[1]:
                filters[i, j] = (right - j) / (right - center)
    return filters


def log_mel_spectrogram(audio: np.ndarray) -> np.ndarray:
    """Whisper log-mel features shaped ``[80, 3000]``."""
    pcm = np.asarray(audio, dtype=np.float32).reshape(-1)
    if pcm.size < WHISPER_N_SAMPLES:
        pcm = np.pad(pcm, (0, WHISPER_N_SAMPLES - pcm.size))
    else:
        pcm = pcm[:WHISPER_N_SAMPLES]
    window = np.hanning(WHISPER_N_FFT).astype(np.float32)
    padded = np.pad(pcm, (WHISPER_N_FFT // 2, WHISPER_N_FFT // 2), mode="reflect")
    n_frames = 1 + (padded.size - WHISPER_N_FFT) // WHISPER_HOP
    spec = np.empty((WHISPER_N_FFT // 2 + 1, n_frames), dtype=np.float32)
    for index in range(n_frames):
        start = index * WHISPER_HOP
        frame = padded[start:start + WHISPER_N_FFT] * window
        spec[:, index] = np.abs(np.fft.rfft(frame, n=WHISPER_N_FFT)) ** 2
    mel = _mel_filters() @ spec
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    if log_spec.shape[1] < 3000:
        log_spec = np.pad(log_spec, ((0, 0), (0, 3000 - log_spec.shape[1])))
    return log_spec[:, :3000].astype(np.float32)


def _decode_tokens(token_ids: list[int], vocab: dict[int, str]) -> str:
    parts = []
    for tid in token_ids:
        piece = vocab.get(tid, "")
        if piece.startswith("<|") and piece.endswith("|>"):
            continue
        parts.append(piece.replace("Ġ", " ").replace("Ċ", "\n"))
    return "".join(parts)


def _whisper_vocab() -> dict[int, str]:
    global _WHISPER_VOCAB
    if _WHISPER_VOCAB is not None:
        return _WHISPER_VOCAB
    path = whisper_cache_dir() / "vocab.json"
    added = whisper_cache_dir() / "added_tokens.json"
    mapping: dict[int, str] = {}
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        mapping.update({int(value): key for key, value in raw.items()})
    if added.is_file():
        extra = json.loads(added.read_text(encoding="utf-8"))
        mapping.update({int(value): key for key, value in extra.items()})
    _WHISPER_VOCAB = mapping
    return mapping


def _ensure_whisper_files() -> bool:
    if whisper_files_present():
        return True
    try:
        from huggingface_hub import snapshot_download
    except Exception:
        return False
    try:
        snapshot_download(
            WHISPER_HF_REPO,
            local_dir=str(whisper_cache_dir()),
            allow_patterns=[
                "model.bin",
                "config.json",
                "tokenizer.json",
                "vocabulary.txt",
                "vocabulary.json",
            ],
        )
        return whisper_files_present()
    except Exception as exc:
        log.info("whisper download skipped: %s", exc)
        return False


def _whisper_device() -> tuple[str, str]:
    """CTranslate2 device. CUDA if the CT2 build sees a GPU; else CPU int8.

    DirectML is not a CTranslate2 backend — Windows-without-CUDA still gets
    int8 CPU, which is far faster than the old greedy ONNX decode loop.
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _whisper_model():
    global _WHISPER_MODEL, _WHISPER_FAILED
    if _WHISPER_FAILED:
        return None
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    if not _ensure_whisper_files():
        _WHISPER_FAILED = True
        return None
    try:
        from faster_whisper import WhisperModel

        device, compute_type = _whisper_device()
        _WHISPER_MODEL = WhisperModel(
            str(whisper_cache_dir()),
            device=device,
            compute_type=compute_type,
        )
        log.info("faster-whisper small on %s/%s", device, compute_type)
        return _WHISPER_MODEL
    except Exception as exc:
        log.warning("faster-whisper load failed: %s", exc)
        _WHISPER_FAILED = True
        return None


def _transcribe_faster_whisper(audio: np.ndarray, *, offset: float = 0.0) -> list[Word]:
    model = _whisper_model()
    if model is None:
        return []
    try:
        segments, _info = model.transcribe(
            audio,
            word_timestamps=True,
            vad_filter=True,
        )
    except Exception as exc:
        log.warning("faster-whisper transcribe failed: %s", exc)
        return []
    words: list[Word] = []
    for segment in segments:
        for item in segment.words or []:
            text = (item.word or "").strip()
            if not text:
                continue
            start = offset + float(item.start)
            dur = max(float(item.end) - float(item.start), 0.02)
            words.append(Word(start, dur, text))
    return words


def persist_transcript(
    session,
    video_id: int,
    fingerprint: str,
    words: list[Word],
    language: str | None = None,
) -> None:
    """Replace phrase rows and FTS content for one video."""
    from selects.db.models import VideoTranscriptSegment
    from selects.util import utcnow

    session.query(VideoTranscriptSegment).filter(VideoTranscriptSegment.video_id == video_id).delete(
        synchronize_session=False
    )
    try:
        session.execute(
            __import__("sqlalchemy").text("DELETE FROM video_transcript_fts WHERE rowid IN "
                                          "(SELECT id FROM video_transcript_segments WHERE video_id = :vid)"),
            {"vid": video_id},
        )
    except Exception:
        pass
    now = utcnow()
    for start, end, text, group in words_to_phrases(words):
        row = VideoTranscriptSegment(
            video_id=video_id,
            start_ms=int(round(start * 1000.0)),
            end_ms=int(round(end * 1000.0)),
            text=text,
            words_json=json.dumps([{"t": w.t, "d": w.d, "w": w.w} for w in group]),
            language=language,
            source_fingerprint=fingerprint,
            processor_version="speech-v1",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        try:
            session.execute(
                __import__("sqlalchemy").text(
                    "INSERT INTO video_transcript_fts(rowid, text) VALUES (:id, :text)"
                ),
                {"id": row.id, "text": text},
            )
        except Exception as exc:
            log.debug("transcript FTS insert skipped: %s", exc)
