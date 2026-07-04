"""Generate Instagram-ready captions for a story using Qwen3-VL.

Takes the story's top-N (by aesthetic) photos as a multi-image prompt, plus
the visit names if known, and asks the VLM for a short caption + 4-6 hashtags.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

from PIL import Image

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Photo, Story, StoryItem, Visit

log = logging.getLogger(__name__)

_MODEL = None
_PROC = None


def _load_qwen():
    global _MODEL, _PROC
    if _MODEL is not None:
        return _MODEL, _PROC
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    name = "Qwen/Qwen3-VL-2B-Instruct"
    _MODEL = AutoModelForCausalLM.from_pretrained(
        name, torch_dtype=torch.bfloat16, device_map="cuda"
    ).eval()
    _PROC = AutoProcessor.from_pretrained(name)
    return _MODEL, _PROC


def generate_caption(cfg: FolderConfig, story_id: int) -> dict:
    """Build a caption + hashtags for a story. Returns dict with both."""
    import torch

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        story = s.get(Story, story_id)
        if not story:
            raise ValueError(f"story {story_id} not found")

        items = (
            s.query(StoryItem, Photo)
            .join(Photo, StoryItem.photo_id == Photo.id)
            .filter(StoryItem.story_id == story_id)
            .order_by(StoryItem.rank)
            .all()
        )
        # Pick 3 sample images by rank order (front, middle, back) for context
        picks: list[tuple[StoryItem, Photo]] = []
        if items:
            picks.append(items[0])
            if len(items) > 2:
                picks.append(items[len(items) // 2])
            if len(items) > 1:
                picks.append(items[-1])

        visit_names: list[str] = []
        for v in s.query(Visit.name).filter(Visit.story_id == story_id).order_by(Visit.rank).all():
            visit_names.append(v[0])

        title = story.title
        day = story.day

    # Load images
    images = []
    for _, photo in picks:
        try:
            img = Image.open(cfg.state_dir / photo.preview_path).convert("RGB")
            images.append(img)
        except Exception as exc:
            log.warning("could not load photo %s: %s", photo.sha256, exc)

    if not images:
        return {"caption": "", "hashtags": [], "error": "no images"}

    model, proc = _load_qwen()

    visits_str = " → ".join(visit_names) if visit_names else "various locations"
    prompt = (
        "These photos are from a single day of a travel trip. "
        f"The visits chronologically: {visits_str}. "
        "Write a short, evocative Instagram caption (1-3 sentences, no emojis, casual tone). "
        "Then on a new line, list 4-6 lowercase hashtags separated by spaces (no #, just words). "
        "Be specific to what's visible — don't repeat the location name unless the photo shows it.\n\n"
        "Format:\n"
        "CAPTION: <text>\n"
        "TAGS: <word1> <word2> ..."
    )

    messages = [
        {
            "role": "user",
            "content": [{"type": "image", "image": img} for img in images]
                       + [{"type": "text", "text": prompt}],
        }
    ]
    text = proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    inputs = proc(
        text=[text],
        images=images,
        padding=True,
        return_tensors="pt",
    ).to("cuda")

    with torch.no_grad():
        out_ids = model.generate(
            **inputs,
            max_new_tokens=200,
            do_sample=False,
        )
    generated = out_ids[:, inputs["input_ids"].shape[1]:]
    decoded = proc.batch_decode(generated, skip_special_tokens=True)[0].strip()

    # Parse the output
    caption = ""
    hashtags: list[str] = []
    for line in decoded.splitlines():
        line = line.strip()
        if line.upper().startswith("CAPTION:"):
            caption = line.split(":", 1)[1].strip()
        elif line.upper().startswith("TAGS:"):
            tags_str = line.split(":", 1)[1].strip()
            hashtags = [t.lstrip("#").lower() for t in tags_str.split() if t]
    if not caption and decoded:
        # Fallback: first line is the caption
        caption = decoded.splitlines()[0].strip()

    return {
        "story_id": story_id,
        "title": title,
        "day": day,
        "caption": caption,
        "hashtags": hashtags,
        "raw": decoded,
    }
