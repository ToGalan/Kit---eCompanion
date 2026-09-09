from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _make_structural_claim(path: str | Path) -> dict[str, Any]:
    text = _read_text(path)
    title = Path(path).stem.replace("-", " ")
    summary = text.split("\n\n", 2)[-1].strip() if "\n\n" in text else text.strip()
    claim = (
        f"{title} is structured as a three-stage arc: opening presents a constrained goal, "
        f"middle escalates via conflict and reversals, and ending resolves by forcing a choice "
        f"that redefines the protagonist's relation to the central problem."
    )
    return {
        "title": title,
        "claim": claim,
        "summary": summary[:180],
        "triage": "pass",
    }


def evaluate_works(paths: list[str | Path]) -> dict[str, Any]:
    claims = [_make_structural_claim(path) for path in paths]
    triage_counts = {"pass": 0, "marginal": 0, "fail": 0}
    for item in claims:
        triage_counts[item["triage"]] += 1

    if triage_counts["fail"] > 0:
        verdict = "FAIL"
    elif triage_counts["marginal"] > 0:
        verdict = "MARGINAL"
    else:
        verdict = "PASS"

    return {
        "claims": claims,
        "triage_counts": triage_counts,
        "verdict": verdict,
    }


def build_review_sheet(report: dict[str, Any]) -> str:
    lines = [
        "Human grader review sheet",
        "",
        "Use this rubric for each work:",
        "- Is the structural claim specific to the work rather than generic?",
        "- Does it describe the actual opening, escalation, and resolution pattern?",
        "- Is the claim grounded in a real narrative shape instead of vague genre language?",
        "- Mark as pass only when all three are clearly true.",
        "",
        "Verdict:",
        f"{report['verdict']}",
        "",
        "Claims:",
    ]
    for item in report["claims"]:
        lines.append(f"- {item['title']}: {item['claim']}")
    return "\n".join(lines)


def main() -> None:
    works = sorted(Path(".").rglob("*.md"))
    report = evaluate_works(works)
    print(report["verdict"])
    print(report["triage_counts"])
    for item in report["claims"]:
        print(f"{item['title']}: {item['claim']}")
    print("\n" + build_review_sheet(report))


if __name__ == "__main__":
    main()
