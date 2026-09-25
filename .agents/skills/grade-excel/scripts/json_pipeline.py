#!/usr/bin/env python3
"""Deterministic JSON pipeline for the payload-first grade-excel skill."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def require_keys(data: dict[str, Any], keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ValueError(f"{context} missing keys: {', '.join(missing)}")


def validate_payload(payload: dict[str, Any]) -> None:
    require_keys(payload, ("rubric", "sheets"), "payload")
    if not isinstance(payload["rubric"], dict) or not isinstance(payload["sheets"], list):
        raise ValueError("payload.rubric must be an object and payload.sheets must be an array")
    names: set[str] = set()
    for sheet in payload["sheets"]:
        if not isinstance(sheet, dict):
            raise ValueError("each sheet must be an object")
        require_keys(sheet, ("sheet_name", "question_start_row", "question_end_row", "questions"), "sheet")
        name = str(sheet["sheet_name"]).strip()
        if not name or name in names:
            raise ValueError(f"sheet_name must be unique and non-empty: {name!r}")
        names.add(name)
        start, end = int(sheet["question_start_row"]), int(sheet["question_end_row"])
        questions = sheet["questions"]
        if start > end or not isinstance(questions, list):
            raise ValueError(f"{name}: invalid question range or questions")
        rows: list[int] = []
        for question in questions:
            if not isinstance(question, dict):
                raise ValueError(f"{name}: question must be an object")
            require_keys(question, ("row", "answer", "rubric_must_have", "rubric_nice_to_have", "rubric_optional"), f"{name} question")
            rows.append(int(question["row"]))
        if rows != list(range(start, end + 1)):
            raise ValueError(f"{name}: question rows must exactly match {start}..{end}")


def safe_clean_tmp(project_dir: Path) -> Path:
    project = project_dir.resolve()
    tmp = (project / ".tmp").resolve()
    if tmp.parent != project:
        raise ValueError("refusing to clean a path outside the current project")
    tmp.mkdir(exist_ok=True)
    for child in tmp.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return tmp


def selected_sheets(payload: dict[str, Any], sheet_name: str | None) -> list[dict[str, Any]]:
    sheets = payload["sheets"]
    if sheet_name is None:
        return sheets
    selected = [sheet for sheet in sheets if sheet["sheet_name"] == sheet_name]
    if len(selected) != 1:
        raise ValueError(f"sheet not found: {sheet_name}")
    return selected


def command_prepare(args: argparse.Namespace) -> None:
    source = Path(args.input).resolve()
    payload = load_json(source)
    validate_payload(payload)
    sheets = selected_sheets(payload, args.sheet)
    tmp = safe_clean_tmp(Path(args.project_dir))
    run_dir = tmp / "grade-excel" / source.stem
    run_dir.mkdir(parents=True, exist_ok=False)
    batches: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(sheets), 5), start=1):
        batch_sheets = sheets[start : start + 5]
        payload_file = run_dir / f"batch-{index:03d}-payload.json"
        partial_file = run_dir / f"batch-{index:03d}-grading.json"
        review_file = run_dir / f"batch-{index:03d}-review-round-{{round}}.json"
        save_json(payload_file, {
            "batch_id": index,
            "source_payload": str(source),
            "rubric": payload["rubric"],
            "sheets": batch_sheets,
        })
        batches.append({
            "batch_id": index,
            "sheet_names": [sheet["sheet_name"] for sheet in batch_sheets],
            "payload_file": str(payload_file),
            "partial_file": str(partial_file),
            "review_file_pattern": str(review_file),
        })
    manifest = {
        "source_payload": str(source),
        "run_dir": str(run_dir),
        "selected_sheet_names": [sheet["sheet_name"] for sheet in sheets],
        "batches": batches,
    }
    manifest_file = run_dir / "manifest.json"
    save_json(manifest_file, manifest)
    print(json.dumps({"manifest_file": str(manifest_file), "batch_count": len(batches)}, ensure_ascii=False))


def expected_sheets(batch: dict[str, Any]) -> dict[str, list[int]]:
    validate_payload({"rubric": batch.get("rubric", {}), "sheets": batch.get("sheets", [])})
    return {sheet["sheet_name"]: [int(question["row"]) for question in sheet["questions"]] for sheet in batch["sheets"]}


def validate_partial(batch: dict[str, Any], partial: dict[str, Any]) -> None:
    require_keys(partial, ("batch_id", "sheets"), "partial")
    if int(partial["batch_id"]) != int(batch["batch_id"]):
        raise ValueError("partial batch_id does not match batch payload")
    expected = expected_sheets(batch)
    actual = partial["sheets"]
    if not isinstance(actual, list) or [sheet.get("sheet_name") for sheet in actual] != list(expected):
        raise ValueError("partial sheets must exactly match assigned batch order")
    for sheet in actual:
        require_keys(sheet, ("sheet_name", "rows", "overall_comment"), "graded sheet")
        if not str(sheet["overall_comment"]).strip():
            raise ValueError(f"{sheet['sheet_name']}: overall_comment is empty")
        rows = sheet["rows"]
        if not isinstance(rows, list) or [int(row.get("row", -1)) for row in rows] != expected[sheet["sheet_name"]]:
            raise ValueError(f"{sheet['sheet_name']}: graded rows do not match assigned rows")
        for row in rows:
            require_keys(row, ("row", "feedback", "score"), "graded row")
            if not str(row["feedback"]).strip():
                raise ValueError(f"{sheet['sheet_name']} row {row['row']}: feedback is empty")
            score = float(row["score"])
            if not 0 <= score <= 10:
                raise ValueError(f"{sheet['sheet_name']} row {row['row']}: score outside 0..10")


def command_validate_partial(args: argparse.Namespace) -> None:
    batch, partial = load_json(Path(args.batch)), load_json(Path(args.partial))
    validate_partial(batch, partial)
    print(json.dumps({"valid": True, "batch_id": batch["batch_id"]}, ensure_ascii=False))


def validate_review(batch: dict[str, Any], review: dict[str, Any]) -> None:
    require_keys(review, ("batch_id", "verdict", "findings"), "review")
    if int(review["batch_id"]) != int(batch["batch_id"]):
        raise ValueError("review batch_id does not match batch payload")
    if review["verdict"] not in {"pass", "changes_required"}:
        raise ValueError("review verdict must be pass or changes_required")
    if not isinstance(review["findings"], list):
        raise ValueError("review findings must be an array")
    if review["verdict"] == "pass" and review["findings"]:
        raise ValueError("pass review must not contain findings")
    if review["verdict"] == "changes_required" and not review["findings"]:
        raise ValueError("changes_required review must contain findings")
    allowed = expected_sheets(batch)
    for finding in review["findings"]:
        require_keys(finding, ("sheet_name", "row", "issue"), "review finding")
        name, row = finding["sheet_name"], int(finding["row"])
        if name not in allowed or row not in allowed[name] or not str(finding["issue"]).strip():
            raise ValueError("review finding does not identify an assigned graded row")
        if "recommended_score" in finding and not 0 <= float(finding["recommended_score"]) <= 10:
            raise ValueError("recommended_score outside 0..10")


def command_validate_review(args: argparse.Namespace) -> None:
    batch, review = load_json(Path(args.batch)), load_json(Path(args.review))
    validate_review(batch, review)
    print(json.dumps({"valid": True, "batch_id": batch["batch_id"], "verdict": review["verdict"]}, ensure_ascii=False))


def summarize_sheet(source: dict[str, Any], graded: dict[str, Any]) -> dict[str, Any]:
    raw = round(sum(float(row["score"]) for row in graded["rows"]), 2)
    count = len(graded["rows"])
    answered = sum(bool(str(question["answer"]).strip()) for question in source["questions"])
    return {
        "sheet_name": graded["sheet_name"], "rows": graded["rows"], "overall_comment": graded["overall_comment"],
        "raw_score": raw, "total_score": round(raw / count, 2), "max_score": count * 10,
        "percentage": round(raw / (count * 10) * 100, 2), "answered_count": answered, "question_count": count,
    }


def report_markdown(final: dict[str, Any]) -> str:
    summary = final["class_summary"]
    lines = ["# Báo cáo chấm", "", f"Nguồn payload: `{final['source_payload']}`", "", "## Tổng hợp lớp", "",
             f"- Điểm thô: **{summary['raw_score']}/{summary['max_score']}**", f"- Điểm tổng: **{summary['total_score']:.2f}/10**",
             f"- Câu có trả lời: **{summary['answered_count']}/{summary['question_count']}**", "", "## Xếp hạng học viên", "",
             "| Hạng | Học viên | Điểm tổng /10 | Điểm thô | Câu có trả lời | Nhận xét tổng quan |", "|---:|---|---:|---:|---:|---|"]
    ranked = sorted(final["sheets"], key=lambda sheet: (-sheet["total_score"], sheet["sheet_name"]))
    for rank, sheet in enumerate(ranked, start=1):
        comment = str(sheet["overall_comment"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {rank} | {sheet['sheet_name']} | {sheet['total_score']:.2f} | {sheet['raw_score']}/{sheet['max_score']} | {sheet['answered_count']}/{sheet['question_count']} | {comment} |")
    return "\n".join(lines) + "\n"


def command_merge(args: argparse.Namespace) -> None:
    manifest = load_json(Path(args.manifest))
    source = load_json(Path(manifest["source_payload"]))
    validate_payload(source)
    source_by_name = {sheet["sheet_name"]: sheet for sheet in source["sheets"]}
    partial_by_name: dict[str, dict[str, Any]] = {}
    for item in manifest["batches"]:
        batch = load_json(Path(item["payload_file"]))
        partial = load_json(Path(item["partial_file"]))
        validate_partial(batch, partial)
        for sheet in partial["sheets"]:
            partial_by_name[sheet["sheet_name"]] = sheet
    sheets = [summarize_sheet(source_by_name[name], partial_by_name[name]) for name in manifest["selected_sheet_names"]]
    raw = round(sum(sheet["raw_score"] for sheet in sheets), 2)
    questions = sum(sheet["question_count"] for sheet in sheets)
    final = {"source_payload": manifest["source_payload"], "rubric": source["rubric"], "sheets": sheets,
             "class_summary": {"raw_score": raw, "total_score": round(raw / questions, 2), "max_score": questions * 10,
                               "percentage": round(raw / (questions * 10) * 100, 2), "answered_count": sum(sheet["answered_count"] for sheet in sheets), "question_count": questions}}
    output, report = Path(args.output), Path(args.report)
    save_json(output, final)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(report_markdown(final), encoding="utf-8")
    print(json.dumps({"output": str(output), "report": str(report)}, ensure_ascii=False))


def command_verify_final(args: argparse.Namespace) -> None:
    manifest = load_json(Path(args.manifest))
    source = load_json(Path(manifest["source_payload"]))
    final = load_json(Path(args.output))
    source_by_name = {sheet["sheet_name"]: sheet for sheet in source["sheets"]}
    expected_names = manifest["selected_sheet_names"]
    if [sheet.get("sheet_name") for sheet in final.get("sheets", [])] != expected_names:
        raise ValueError("final sheets do not match manifest order")
    expected_summaries: list[dict[str, Any]] = []
    for sheet in final["sheets"]:
        name = sheet["sheet_name"]
        if name not in source_by_name:
            raise ValueError(f"final contains unexpected sheet: {name}")
        source_sheet = source_by_name[name]
        rows = sheet.get("rows")
        expected_rows = [int(question["row"]) for question in source_sheet["questions"]]
        if not isinstance(rows, list) or [int(row.get("row", -1)) for row in rows] != expected_rows:
            raise ValueError(f"{name}: final rows do not match source")
        for row in rows:
            if not str(row.get("feedback", "")).strip() or not 0 <= float(row.get("score", -1)) <= 10:
                raise ValueError(f"{name}: invalid final feedback or score")
        recomputed = summarize_sheet(source_sheet, sheet)
        for key in ("raw_score", "total_score", "max_score", "percentage", "answered_count", "question_count"):
            if sheet.get(key) != recomputed[key]:
                raise ValueError(f"{name}: {key} does not match computed value")
        expected_summaries.append(recomputed)
    raw = round(sum(sheet["raw_score"] for sheet in expected_summaries), 2)
    questions = sum(sheet["question_count"] for sheet in expected_summaries)
    expected_class = {"raw_score": raw, "total_score": round(raw / questions, 2), "max_score": questions * 10,
                      "percentage": round(raw / (questions * 10) * 100, 2),
                      "answered_count": sum(sheet["answered_count"] for sheet in expected_summaries), "question_count": questions}
    if final.get("class_summary") != expected_class:
        raise ValueError("class_summary does not match computed value")
    if Path(args.report).read_text(encoding="utf-8") != report_markdown(final):
        raise ValueError("Markdown report does not match final JSON")
    print(json.dumps({"valid": True, "sheet_count": len(expected_summaries)}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic helpers for JSON-only grade-excel.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--input", required=True); prepare.add_argument("--project-dir", required=True); prepare.add_argument("--sheet")
    prepare.set_defaults(func=command_prepare)
    partial = commands.add_parser("validate-partial")
    partial.add_argument("--batch", required=True); partial.add_argument("--partial", required=True); partial.set_defaults(func=command_validate_partial)
    review = commands.add_parser("validate-review")
    review.add_argument("--batch", required=True); review.add_argument("--review", required=True); review.set_defaults(func=command_validate_review)
    merge = commands.add_parser("merge")
    merge.add_argument("--manifest", required=True); merge.add_argument("--output", required=True); merge.add_argument("--report", required=True); merge.set_defaults(func=command_merge)
    final = commands.add_parser("verify-final")
    final.add_argument("--manifest", required=True); final.add_argument("--output", required=True); final.add_argument("--report", required=True); final.set_defaults(func=command_verify_final)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"ERROR: {error}")
