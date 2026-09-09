from pathlib import Path

from evaluate import build_review_sheet, evaluate_works


def _write_work(path: Path, title: str, body: str):
    path.write_text(
        "---\ntitle: %s\n---\n\n# %s\n\n%s\n" % (title, title, body),
        encoding="utf-8",
    )


def test_evaluate_works_returns_20_structural_claims_and_verdict(tmp_path):
    works = []
    for i in range(20):
        title = f"Work {i + 1}"
        body = (
            "The story opens with a small city and a protagonist who wants to escape. "
            "The middle escalates through repeated betrayals, and the ending resolves the core conflict "
            "by forcing a choice between identity and loyalty."
        )
        path = tmp_path / f"{title.replace(' ', '-')}.md"
        _write_work(path, title, body)
        works.append(path)

    report = evaluate_works(works)

    assert len(report["claims"]) == 20
    assert set(report["triage_counts"]) == {"pass", "marginal", "fail"}
    assert report["verdict"] in {"PASS", "MARGINAL", "FAIL"}


def test_human_review_sheet_is_printable_and_includes_rubric(tmp_path):
    work = tmp_path / "review-target.md"
    _write_work(
        work,
        "Review Target",
        "The film opens with a struggling inventor, the middle escalates through repeated setbacks, and the ending resolves the central conflict by making the costs explicit.",
    )

    report = evaluate_works([work])
    sheet = build_review_sheet(report)

    assert "Human grader review sheet" in sheet
    assert "specific" in sheet.lower()
    assert "structural" in sheet.lower()
