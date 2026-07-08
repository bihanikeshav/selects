"""SigLIP text tokenizer — torch/transformers-free.

Ports transformers' ``SiglipTokenizer`` onto plain ``sentencepiece`` so runtime
text encoding (search queries, cluster-vocab, IQA prompts) needs neither torch
nor transformers. The sentencepiece model (``spiece.model``) is fetched from the
shared HF ONNX repo. Token-id output is verified byte-for-byte against
transformers (see scratchpad verify_siglip_tok.py).

SigLIP specifics reproduced here (verified byte-for-byte against transformers'
SiglipTokenizer over emoji / unicode / punctuation / empty / 300-char inputs):
* canonicalize: lower-case, strip ASCII punctuation, collapse whitespace, strip;
* sentencepiece encode (keeps the default ``▁`` prefix on the first piece);
* append eos (``</s>`` = id 1) then pad/truncate to 64. eos and pad share id 1.
"""
from __future__ import annotations

import re
import string
from functools import lru_cache

import numpy as np

_MAX_LEN = 64
_EOS_ID = 1
_PAD_ID = 1
_PUNCT = str.maketrans("", "", string.punctuation)
_WS = re.compile(r"\s+")


class SiglipTokenizer:
    def __init__(self, spiece_path: str, max_length: int = _MAX_LEN):
        import sentencepiece as spm

        self.sp = spm.SentencePieceProcessor(model_file=spiece_path)
        self.max_length = max_length

    def _canonicalize(self, text: str) -> str:
        text = text.lower().translate(_PUNCT)
        return _WS.sub(" ", text).strip()

    def _encode_one(self, text: str) -> list[int]:
        # leave room for eos, then always terminate with eos, then pad
        ids = self.sp.encode(self._canonicalize(text), out_type=int)[: self.max_length - 1]
        ids = ids + [_EOS_ID]
        ids += [_PAD_ID] * (self.max_length - len(ids))
        return ids

    def __call__(self, texts: list[str]) -> np.ndarray:
        """Return [N, 64] int64 input_ids, matching SiglipTokenizer(padding='max_length')."""
        return np.asarray([self._encode_one(t) for t in texts], dtype=np.int64)


@lru_cache(maxsize=1)
def get_tokenizer() -> SiglipTokenizer:
    """Cached tokenizer built from the spiece.model in the shared HF ONNX repo."""
    from selects.ml.onnx_rt import repo_file

    return SiglipTokenizer(repo_file("spiece.model"))
