from unittest.mock import Mock

import pytest

from selects.selection import choose_diverse


def reference(items, limit, scores, features, previous, penalty):
    remaining = dict(items)
    selected = [features[i] for i in previous]
    output = []
    while remaining and len(output) < max(0, limit):
        def utility(i):
            own = features[i]
            overlap = max((len(own & other) / len(own | other) if own or other else 0
                           for other in selected), default=0)
            return scores[i] - penalty * overlap, -i
        winner = max(remaining, key=utility)
        output.append(remaining.pop(winner))
        selected.append(features[winner])
    return output


@pytest.mark.parametrize("penalty", [0, .12, 1.2])
@pytest.mark.parametrize("limit", [0, 1, 12, 40, 100])
def test_optimized_ranking_matches_reference(penalty, limit):
    ids = list(reversed(range(50)))
    scores = {i: (i % 7) / 7 for i in ids}
    features = {i: frozenset() if i % 11 == 0 else frozenset({
        ("day", str(i % 3)), ("person", str(i % 5)), ("scene", str(i % 4)),
    }) for i in ids}
    previous = [3, 8]
    actual = choose_diverse(ids, limit, score=scores.__getitem__,
                            features=features.__getitem__, identity=lambda i: i,
                            previous=previous, penalty=penalty)
    expected = reference([(i, i) for i in ids], limit, scores, features, previous, penalty)
    assert actual == expected


def test_score_only_never_evaluates_diversity_and_scores_each_candidate_once():
    ids = list(reversed(range(3000)))
    features = Mock(side_effect=AssertionError("score-only mode must not read diversity metadata"))
    score = Mock(side_effect=lambda i: i // 2)
    result = choose_diverse(ids, 80, score=score, features=features,
                            identity=lambda i: i, previous=[4000], penalty=0)
    assert result == sorted(ids, key=lambda i: (-(i // 2), i))[:80]
    assert score.call_count == len(ids)
    features.assert_not_called()


def test_balanced_ranking_evaluates_scores_and_features_once():
    ids = list(range(300))
    score = Mock(side_effect=lambda i: i / 300)
    features = Mock(side_effect=lambda i: frozenset({("day", str(i % 10))}))
    assert len(choose_diverse(ids, 80, score=score, features=features, identity=lambda i: i)) == 80
    assert score.call_count == features.call_count == len(ids)
