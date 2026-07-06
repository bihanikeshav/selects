from selects.classical.auto_reject import RejectInput, evaluate_reject


def test_sharp_balanced_image_not_rejected():
    inp = RejectInput(blur=2000.0, exposure_score=0.6, clipped_ratio=0.02, faces_count=1)
    result = evaluate_reject(inp)
    assert result.auto_reject is False
    assert result.reason is None


def test_severe_blur_triggers_reject():
    inp = RejectInput(blur=10.0, exposure_score=0.5, clipped_ratio=0.05, faces_count=0)
    result = evaluate_reject(inp)
    assert result.auto_reject is True
    assert result.reason == "severe_blur"


def test_blown_out_triggers_reject():
    inp = RejectInput(blur=2000.0, exposure_score=0.0, clipped_ratio=0.99, faces_count=0)
    result = evaluate_reject(inp)
    assert result.auto_reject is True
    assert result.reason in ("blown_out", "all_black")
