"""Evaluation harness for the Kit matching engine.

This harness measures the recommendation layer against held-out engagement data,
not against synthetic claims or hand-picked examples. It reports hit@10 with
baseline comparisons, bootstrap confidence intervals, and seed variance.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from mind.matching import match


class Triage(str, Enum):
    PASS = "pass"
    MARGINAL = "marginal"
    FAIL = "fail"


DEFAULT_SEEDS = (0, 1, 2, 3, 4)


def _read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _mean(values: Iterable[float]) -> float:
    values = [float(value) for value in values]
    return sum(values) / len(values) if values else 0.0


def _variance(values: Iterable[float]) -> float:
    values = [float(value) for value in values]
    if len(values) < 2:
        return 0.0
    return statistics.pvariance(values)


def _bootstrap_ci(values: Sequence[float], *, resamples: int = 2000, confidence: float = 0.95, seed: int = 0) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)

    rng = random.Random(seed)
    sample = [float(value) for value in values]
    boot_means: list[float] = []
    for _ in range(resamples):
        drawn = [sample[rng.randrange(len(sample))] for _ in sample]
        boot_means.append(_mean(drawn))

    boot_means.sort()
    lower_index = max(0, math.floor((1.0 - confidence) / 2.0 * len(boot_means)))
    upper_index = min(len(boot_means) - 1, math.ceil((1.0 - (1.0 - confidence) / 2.0) * len(boot_means)) - 1)
    return float(boot_means[lower_index]), float(boot_means[upper_index])


def _engaged_titles(entry: dict[str, Any]) -> set[str]:
    engaged = entry.get("engaged", []) or []
    titles: set[str] = set()
    for item in engaged:
        if isinstance(item, str):
            title = item.strip()
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or "").strip()
        else:
            title = str(item).strip()
        if title:
            titles.add(title)
    return titles


def _candidate_titles(entry: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = entry.get("catalog") or entry.get("candidates") or []
    if not isinstance(catalog, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        candidate = dict(item)
        candidate["title"] = title
        cleaned.append(candidate)
    return cleaned


def _persona_from_signals(signals: Iterable[Any]) -> dict[str, Any]:
    elicited: list[dict[str, str]] = []
    signal_text_parts: list[str] = []
    for index, signal in enumerate(signals):
        if isinstance(signal, dict):
            title = str(signal.get("title") or f"signal-{index + 1}").strip()
            content = str(signal.get("content") or signal.get("text") or signal.get("summary") or "").strip()
        else:
            title = f"signal-{index + 1}"
            content = str(signal).strip()
        if not content:
            continue
        elicited.append({"title": title, "content": content})
        signal_text_parts.append(content)

    return {
        "elicited": elicited,
        "active_hypotheses": [
            {
                "title": "functional-fit hypothesis",
                "function": "calm recovery, focus, or sustained engagement",
                "content": " ".join(signal_text_parts),
            }
        ],
    }


def _score_hit_at_k(ranked: list[Any], engaged_titles: set[str], *, k: int) -> int:
    seen = 0
    for item in ranked[:k]:
        if hasattr(item, "title"):
            title = str(item.title).strip()
        elif isinstance(item, dict):
            title = str(item.get("title", "")).strip()
        else:
            title = str(item).strip()
        if title and title in engaged_titles:
            seen = 1
            break
    return seen


def evaluate_matching(
    dataset: Sequence[dict[str, Any]],
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    k: int = 10,
    n_signals: int | None = None,
) -> dict[str, Any]:
    """Evaluate the function-based matching engine against held-out engagement.

    Each row in dataset should look like:
      {
        "user_id": "u1",
        "signals": [...],
        "engaged": [...],
        "catalog": [{"title": ..., "functions": [...], "wrong_if": ..., "popularity": ...}, ...]
      }
    """
    if not dataset:
        return {
            "metric": "hit@10",
            "persona": {"mean": 0.0, "ci": [0.0, 0.0], "variance": 0.0, "seed_values": []},
            "random": {"mean": 0.0, "ci": [0.0, 0.0], "variance": 0.0, "seed_values": []},
            "popularity": {"mean": 0.0, "ci": [0.0, 0.0], "variance": 0.0, "seed_values": []},
            "seeds_used": [],
            "verdict": "FAIL: no evaluation data was provided.",
            "json_report": "{\"metric\": \"hit@10\", \"verdict\": \"FAIL: no evaluation data was provided.\"}",
            "human_review_sheet": "Human grader review sheet\n\nVerdict:\nFAIL: no evaluation data was provided.",
        }

    seed_values: list[dict[str, Any]] = []
    persona_seed_means: list[float] = []
    random_seed_means: list[float] = []
    popularity_seed_means: list[float] = []

    for seed in seeds:
        persona_scores: list[int] = []
        random_scores: list[int] = []
        popularity_scores: list[int] = []

        for index, entry in enumerate(dataset):
            engaged_titles = _engaged_titles(entry)
            if not engaged_titles:
                continue

            catalog = _candidate_titles(entry)
            if not catalog:
                continue

            signals = list(entry.get("signals") or [])
            if n_signals is not None:
                signals = signals[:n_signals]
            persona_snapshot = _persona_from_signals(signals)

            ranked = match(persona_snapshot, catalog, k=k)
            persona_hit = _score_hit_at_k(ranked, engaged_titles, k=k)
            persona_scores.append(persona_hit)

            rng = random.Random(seed + index)
            random_sample = rng.sample(catalog, min(k, len(catalog)))
            random_hit = _score_hit_at_k(random_sample, engaged_titles, k=k)
            random_scores.append(random_hit)

            ranked_popularity = sorted(catalog, key=lambda item: float(item.get("popularity", 0.0)), reverse=True)
            popularity_hit = _score_hit_at_k(ranked_popularity, engaged_titles, k=k)
            popularity_scores.append(popularity_hit)

        persona_mean = _mean(persona_scores) if persona_scores else 0.0
        random_mean = _mean(random_scores) if random_scores else 0.0
        popularity_mean = _mean(popularity_scores) if popularity_scores else 0.0
        persona_seed_means.append(persona_mean)
        random_seed_means.append(random_mean)
        popularity_seed_means.append(popularity_mean)

        seed_values.append(
            {
                "seed": int(seed),
                "persona_hit@10": persona_mean,
                "random_hit@10": random_mean,
                "popularity_hit@10": popularity_mean,
            }
        )

    persona_ci = _bootstrap_ci(persona_seed_means, seed=DEFAULT_SEEDS[0] if DEFAULT_SEEDS else 0)
    random_ci = _bootstrap_ci(random_seed_means, seed=DEFAULT_SEEDS[0] if DEFAULT_SEEDS else 0)
    popularity_ci = _bootstrap_ci(popularity_seed_means, seed=DEFAULT_SEEDS[0] if DEFAULT_SEEDS else 0)

    persona_mean = _mean(persona_seed_means)
    random_mean = _mean(random_seed_means)
    popularity_mean = _mean(popularity_seed_means)

    if persona_mean <= random_mean or persona_mean <= popularity_mean:
        verdict = (
            f"FAIL: persona hit@10 ({persona_mean:.3f}) did not beat random ({random_mean:.3f}) "
            f"or popularity ({popularity_mean:.3f})."
        )
    elif persona_mean > random_mean and persona_mean > popularity_mean:
        verdict = (
            f"PASS: persona hit@10 ({persona_mean:.3f}) beat random ({random_mean:.3f}) and "
            f"popularity ({popularity_mean:.3f})."
        )
    else:
        verdict = (
            f"MARGINAL: persona hit@10 ({persona_mean:.3f}) beat one baseline but not the other. "
            f"Random={random_mean:.3f}, popularity={popularity_mean:.3f}."
        )

    report: dict[str, Any] = {
        "metric": "hit@10",
        "persona": {
            "mean": round(persona_mean, 6),
            "ci": [round(persona_ci[0], 6), round(persona_ci[1], 6)],
            "variance": round(_variance(persona_seed_means), 6),
            "seed_values": [round(value, 6) for value in persona_seed_means],
        },
        "random": {
            "mean": round(random_mean, 6),
            "ci": [round(random_ci[0], 6), round(random_ci[1], 6)],
            "variance": round(_variance(random_seed_means), 6),
            "seed_values": [round(value, 6) for value in random_seed_means],
        },
        "popularity": {
            "mean": round(popularity_mean, 6),
            "ci": [round(popularity_ci[0], 6), round(popularity_ci[1], 6)],
            "variance": round(_variance(popularity_seed_means), 6),
            "seed_values": [round(value, 6) for value in popularity_seed_means],
        },
        "seeds_used": [int(seed) for seed in seeds],
        "verdict": verdict,
    }

    report["json_report"] = json.dumps(report, sort_keys=True, indent=2)
    report["human_review_sheet"] = build_review_sheet(report)
    return report


def build_review_sheet(report: dict[str, Any]) -> str:
    lines = [
        "Human grader review sheet",
        "",
        "Held-out evaluation of the persona matching engine against actual engagement.",
        "",
        f"Verdict: {report.get('verdict', 'UNKNOWN')}",
        "",
        "Primary metric: hit@10",
        "",
        f"Persona mean hit@10: {report.get('persona', {}).get('mean', 0.0):.3f} 95% CI {report.get('persona', {}).get('ci', [0.0, 0.0])[0]:.3f} to {report.get('persona', {}).get('ci', [0.0, 0.0])[1]:.3f}",
        f"Random mean hit@10: {report.get('random', {}).get('mean', 0.0):.3f} 95% CI {report.get('random', {}).get('ci', [0.0, 0.0])[0]:.3f} to {report.get('random', {}).get('ci', [0.0, 0.0])[1]:.3f}",
        f"Popularity mean hit@10: {report.get('popularity', {}).get('mean', 0.0):.3f} 95% CI {report.get('popularity', {}).get('ci', [0.0, 0.0])[0]:.3f} to {report.get('popularity', {}).get('ci', [0.0, 0.0])[1]:.3f}",
        f"Seed variance (persona): {report.get('persona', {}).get('variance', 0.0):.4f}",
        f"Seeds used: {report.get('seeds_used', [])}",
        "",
        "Interpretation:",
        "- The persona match is judged against a held-out engagement set, not against the training signals.",
        "- Random and popularity are computed on the same candidate pool and the same held-out users.",
        "- A report is only valid when all three appear alongside one another.",
    ]
    return "\n".join(lines)


def _make_structural_claim(path: str | Path) -> dict[str, Any]:
    raise NotImplementedError("structural claim generation requires a model; see mind/ai.py")


def evaluate_works(paths: list[str | Path]) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    triage_counts = {state.value: 0 for state in Triage}

    for entry in paths:
        if isinstance(entry, dict):
            item = dict(entry)
            triage = Triage(str(item.get("triage", Triage.FAIL.value)))
            item["triage"] = triage.value
            item.setdefault("title", Path(str(item.get("path", "untitled"))).stem.replace("-", " "))
            item.setdefault("claim", "")
            claims.append(item)
            triage_counts[triage.value] += 1
            continue

        _make_structural_claim(entry)

    if triage_counts[Triage.FAIL.value] > 0:
        verdict = "FAIL"
    elif triage_counts[Triage.MARGINAL.value] > 0:
        verdict = "MARGINAL"
    else:
        verdict = "PASS"

    return {
        "claims": claims,
        "triage_counts": triage_counts,
        "verdict": verdict,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python evaluate.py <dataset.json>")

    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    report = evaluate_matching(payload)
    print(report["json_report"])
    print()
    print(report["human_review_sheet"])


if __name__ == "__main__":
    main()
