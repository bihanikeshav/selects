"""SigLIP 2 text tokenizer — torch/transformers-free.

SigLIP 2 uses a Gemma sentencepiece vocab (256k). The HF processor lower-cases,
appends ``<eos>`` (id 1), and pads with ``<pad>`` (id 0) to 64 tokens. We do
not strip punctuation (that was SigLIP 1 canonicalize).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

_MAX_LEN = 64
_EOS_ID = 1
_PAD_ID = 0


class SiglipTokenizer:
    def __init__(
        self,
        spiece_path: str,
        max_length: int = _MAX_LEN,
        *,
        eos_id: int = _EOS_ID,
        pad_id: int = _PAD_ID,
    ):
        import sentencepiece as spm

        self.sp = spm.SentencePieceProcessor(model_file=spiece_path)
        self.max_length = max_length
        self.eos_id = eos_id
        self.pad_id = pad_id

    def _encode_one(self, text: str) -> list[int]:
        ids = self.sp.encode(text.lower(), out_type=int)[: self.max_length - 1]
        ids = ids + [self.eos_id]
        ids += [self.pad_id] * (self.max_length - len(ids))
        return ids

    def __call__(self, texts: list[str]) -> np.ndarray:
        """Return [N, 64] int64 input_ids (right-padded)."""
        return np.asarray([self._encode_one(t) for t in texts], dtype=np.int64)


@lru_cache(maxsize=1)
def get_tokenizer() -> SiglipTokenizer:
    """Cached tokenizer from the SigLIP 2 asset folder."""
    from selects.ml.model_assets import asset_dir, asset_present, download_one, MANIFEST

    path = asset_dir("siglip2") / "tokenizer.model"
    if not path.is_file() or path.stat().st_size <= 0:
        asset = next(item for item in MANIFEST if item["id"] == "siglip2")
        if not asset_present(asset):
            download_one("siglip2")
        path = asset_dir("siglip2") / "tokenizer.model"
    return SiglipTokenizer(str(path))
