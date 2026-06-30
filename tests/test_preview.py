from pathlib import Path

import numpy as np

from travelcull.indexer.preview import write_previews


def test_write_previews_creates_two_files(tmp_path):
    img = (np.random.rand(2000, 1500, 3) * 255).astype(np.uint8)
    thumb_path, preview_path = write_previews(
        img, sha256="abc123", thumbs_dir=tmp_path / "thumbs", previews_dir=tmp_path / "prev"
    )
    assert thumb_path.exists()
    assert preview_path.exists()
    assert thumb_path.suffix == ".jpg"


def test_thumb_is_256_max(tmp_path):
    img = (np.random.rand(4000, 3000, 3) * 255).astype(np.uint8)
    thumb_path, _ = write_previews(
        img, sha256="def", thumbs_dir=tmp_path / "t", previews_dir=tmp_path / "p"
    )
    from PIL import Image

    with Image.open(thumb_path) as im:
        assert max(im.size) == 256


def test_preview_is_1024_max(tmp_path):
    img = (np.random.rand(4000, 3000, 3) * 255).astype(np.uint8)
    _, preview_path = write_previews(
        img, sha256="ghi", thumbs_dir=tmp_path / "t", previews_dir=tmp_path / "p"
    )
    from PIL import Image

    with Image.open(preview_path) as im:
        assert max(im.size) == 1024
