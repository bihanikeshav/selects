"""
bench_vlms.py — VL model head-to-head benchmark for cluster naming.

Models tested:
  - Qwen/Qwen3-VL-2B-Instruct  (cached)
  - Qwen/Qwen3-VL-4B-Instruct  (cached, loads with device_map='auto' if 8GB VRAM is tight)

Gemma-4-E4B-it: 16 GB BF16 download — exceeds 15 GB bandwidth budget.
Qwen3.5-VL: does not exist as a VL-specific family (Qwen3.5 is text-only).

Task: name a cluster of 5 Ladakh monastery-hillside photos.
"""

import os
import sys
import io
import time
import sqlite3
import gc
import traceback
from pathlib import Path
from PIL import Image

# Force UTF-8 stdout so box-drawing chars and non-ASCII model output don't crash on cp1252
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import torch
from transformers import AutoProcessor, AutoModelForImageTextToText, Qwen3VLForConditionalGeneration

DB_PATH    = r"Z:\Ladakh\Photos\.travelcull\index.db"
PREV_BASE  = r"Z:\Ladakh\Photos\.travelcull\previews"
CLUSTER    = "monastery hillside"   # 11 photos, clearly distinctive
N_PHOTOS   = 5

PROMPT = (
    "These photos share a visual theme. "
    "Describe the common theme in 2-4 words, lowercase, no period. "
    "Be specific to what's visually distinctive "
    "(e.g., \"monastery courtyard\" beats \"buddhist temple\", "
    "\"yak in pasture\" beats \"animal\", "
    "\"prayer flags on pass\" beats \"outdoor scene\"). "
    "Output only the label, nothing else."
)

MODELS = [
    {
        "id": "Qwen/Qwen3-VL-2B-Instruct",
        "label": "Qwen3-VL-2B",
        "dtype": torch.bfloat16,
        "device_map": "cuda",
    },
    {
        "id": "Qwen/Qwen3-VL-4B-Instruct",
        "label": "Qwen3-VL-4B",
        "dtype": torch.bfloat16,
        # 4B BF16 = ~8.9 GB weights; 8.55 GB VRAM. Allow auto-offload to CPU.
        "device_map": "auto",
    },
]


def get_cluster_photos(cluster_tag: str, n: int) -> list[str]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        SELECT p.sha256
        FROM photo_tags pt
        JOIN photos p ON pt.photo_id = p.id
        WHERE pt.tag = ?
        LIMIT ?
        """,
        (cluster_tag, n),
    )
    rows = cur.fetchall()
    con.close()
    paths = []
    for (sha,) in rows:
        p = os.path.join(PREV_BASE, sha + ".jpg")
        if os.path.exists(p):
            paths.append(p)
    return paths[:n]


def vram_mb() -> float:
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / 1e6
    return 0.0


def peak_vram_mb() -> float:
    if torch.cuda.is_available():
        return torch.cuda.max_memory_allocated() / 1e6
    return 0.0


def run_model(cfg: dict, photos: list[str]) -> dict:
    model_id   = cfg["id"]
    dtype      = cfg["dtype"]
    device_map = cfg["device_map"]
    label      = cfg["label"]

    result = {
        "model": label,
        "model_id": model_id,
        "output": None,
        "load_s": None,
        "infer_s": None,
        "peak_vram_mb": None,
        "error": None,
    }

    try:
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.empty_cache()

        # --- Load ---
        t0 = time.perf_counter()
        processor = AutoProcessor.from_pretrained(model_id)
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            model_id,
            torch_dtype=dtype,
            device_map=device_map,
        )
        model.eval()
        load_s = time.perf_counter() - t0
        result["load_s"] = round(load_s, 2)

        # --- Build conversation with all images ---
        images = [Image.open(p).convert("RGB") for p in photos]

        # Each image gets an image content block; all share one text turn.
        content = []
        for img in images:
            content.append({"type": "image", "image": img})
        content.append({"type": "text", "text": PROMPT})

        messages = [{"role": "user", "content": content}]

        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        # The Qwen3-VL processor expects image_inputs and video_inputs separately
        from qwen_vl_utils import process_vision_info
        image_inputs, video_inputs = process_vision_info(messages)

        inputs = processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            return_tensors="pt",
            padding=True,
        )

        # Move to model device
        if device_map == "cuda":
            inputs = {k: v.to("cuda") if hasattr(v, "to") else v for k, v in inputs.items()}
        else:
            # device_map='auto' — move to the first cuda device
            device = next(model.parameters()).device
            inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}

        # --- Inference ---
        t1 = time.perf_counter()
        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=20,
                do_sample=False,
            )
        infer_s = time.perf_counter() - t1

        # Decode only generated tokens
        generated = out_ids[:, inputs["input_ids"].shape[1]:]
        decoded = processor.batch_decode(generated, skip_special_tokens=True)[0].strip()

        result["output"]       = decoded
        result["infer_s"]      = round(infer_s, 2)
        result["peak_vram_mb"] = round(peak_vram_mb(), 0)

    except Exception:
        result["error"] = traceback.format_exc()

    finally:
        # Free VRAM before next model
        try:
            del model
        except Exception:
            pass
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return result


def main():
    print(f"\n=== bench_vlms.py — Cluster naming benchmark ===")
    print(f"Cluster: '{CLUSTER}' | Photos: {N_PHOTOS}")
    print(f"Device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")
    total_vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9 if torch.cuda.is_available() else 0
    print(f"VRAM: {total_vram_gb:.2f} GB\n")

    photos = get_cluster_photos(CLUSTER, N_PHOTOS)
    if not photos:
        print(f"ERROR: No photos found for cluster '{CLUSTER}'. Check DB path.")
        sys.exit(1)
    print(f"Photos loaded ({len(photos)}):")
    for p in photos:
        print(f"  {p}")
    print()

    results = []
    for cfg in MODELS:
        print(f"--- Running {cfg['label']} ({cfg['id']}) ---")
        r = run_model(cfg, photos)
        results.append(r)
        if r["error"]:
            print(f"  ERROR: {r['error'][:300]}")
        else:
            print(f"  Output:     {r['output']}")
            print(f"  Load:       {r['load_s']} s")
            print(f"  Infer:      {r['infer_s']} s")
            print(f"  Peak VRAM:  {r['peak_vram_mb']} MB")
        print()

    # Summary table
    print("\n=== RESULTS TABLE ===")
    header = f"{'Model':<20} {'Output':<30} {'Load (s)':<10} {'Infer (s)':<10} {'Peak VRAM':<12}"
    print(header)
    print("-" * len(header))
    for r in results:
        if r["error"]:
            row = f"{r['model']:<20} {'ERROR':<30} {'—':<10} {'—':<10} {'—':<12}"
        else:
            vram_str = f"{int(r['peak_vram_mb'])} MB" if r['peak_vram_mb'] else "—"
            row = f"{r['model']:<20} {str(r['output']):<30} {r['load_s']:<10} {r['infer_s']:<10} {vram_str:<12}"
        print(row)


if __name__ == "__main__":
    main()
