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
        mirror_files: list[dict[str, str]] = []
        for sheet in batch_sheets:
            # One output file per grader: shared-file append caused lost updates
            # when graders ran in parallel (race condition). Graders write only
            # their own mirror file; the shared file is validated against them.
            mirror_file = run_dir / f"partial-{index:03d}-{sheet['sheet_name']}-grading.json"
            save_json(mirror_file, {"batch_id": index, "sheets": []})
            mirror_files.append({"sheet_name": sheet["sheet_name"], "file": str(mirror_file)})
        # Persist into the batch payload itself so any stage can self-serve these.
        payload_data = load_json(payload_file)
        payload_data["sheet_names"] = [sheet["sheet_name"] for sheet in batch_sheets]
        payload_data["mirror_files"] = mirror_files
        payload_data["partial_file"] = str(partial_file)
        save_json(payload_file, payload_data)
        batches.append({
            "batch_id": index,
            "sheet_names": [sheet["sheet_name"] for sheet in batch_sheets],
            "payload_file": str(payload_file),
            "partial_file": str(partial_file),
            "mirror_files": mirror_files,
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


def validate_mirror_files(batch: dict[str, Any], no_shared: bool = False) -> list[dict[str, Any]]:
    """Validate per-grader mirror files against the shared partial file.

    Each grader must write exactly one sheet into its OWN mirror file. The
    shared partial file must contain the union of all mirror sheets in
    batch order. Detects racing caused lost updates when graders write into a
    single shared file. Returns the validated sheet list in batch order.
    """
    signature = lambda sheet: (
        sheet.get("sheet_name"),
        [int(row.get("row", -1)) for row in sheet.get("rows", [])],
        tuple((row.get("feedback"), row.get("score")) for row in sheet.get("rows", [])),
        sheet.get("overall_comment"),
    )
    union: dict[str, dict[str, Any]] = {}
    expected = expected_sheets(batch)
    for mirror in batch.get("mirror_files", []):
        file = Path(mirror["file"])
        if not file.is_file():
            raise ValueError(f"mirror file missing (grader may have died): {file}")
        data = load_json(file)
        require_keys(data, ("batch_id", "sheets"), "mirror file")
        if int(data["batch_id"]) != int(batch["batch_id"]):
            raise ValueError("mirror file batch_id does not match batch payload")
        sheets = data["sheets"]
        if not isinstance(sheets, list) or len(sheets) != 1:
            raise ValueError(f"mirror file must contain exactly one sheet: {file.name}")
        sheet = sheets[0]
        if not isinstance(sheet, dict) or sheet.get("sheet_name") != mirror["sheet_name"]:
            raise ValueError(f"mirror file sheet_name does not match assignment: {file.name}")
        validate_single_sheet(expected, sheet, payload_batch=batch, strict=True)
        if not no_shared and mirror["sheet_name"] in union:
            raise ValueError(f"duplicate sheet in mirrors: {mirror['sheet_name']}")
        union[mirror["sheet_name"]] = sheet
    missing = [name for name in batch["sheet_names"] if name not in union]
    if missing:
        raise ValueError(f"mirror files missing sheets: {', '.join(missing)}")
    if not no_shared and batch.get("partial_file"):
        partial_file = Path(batch["partial_file"])
        if partial_file.is_file():
            partial = load_json(partial_file)
            if int(partial.get("batch_id", -1)) != int(batch["batch_id"]) or [s.get("sheet_name") for s in partial.get("sheets", [])] != batch["sheet_names"]:
                raise ValueError("shared partial file does not match batch order — race condition detected")
            expected = {s.get("sheet_name"): s for s in partial.get("sheets", [])}
            for name in batch["sheet_names"]:
                if signature(expected.get(name)) != signature(union[name]):
                    raise ValueError(f"shared partial sheet differs from mirror file: {name} — race condition detected")
    return [union[name] for name in batch["sheet_names"]]


_STUB_MARKERS = ("stub", "(stub for validate)", "placeholder", "todo", "điền sau")


def validate_single_sheet(expected: dict[str, list[int]], sheet: dict[str, Any], payload_batch: dict[str, Any] | None = None, strict: bool = False) -> None:
    """Validate one graded sheet against its expected row list.

    Gate flags (strict=True turns them all on):
    1. Stub detection: feedback / overall_comment containing template markers is rejected.
    2. Blank-consistency: a graded score of 0 is only allowed with the exact
       empty-answer feedback AND a blank answer in the source payload.
    """
    require_keys(sheet, ("sheet_name", "rows", "overall_comment"), "graded sheet")
    name = sheet["sheet_name"]
    if name not in expected:
        raise ValueError(f"sheet not assigned in this batch: {name}")
    comment = str(sheet["overall_comment"])
    if not comment.strip():
        raise ValueError(f"{name}: overall_comment is empty")
    if strict and any(marker in comment.lower() for marker in _STUB_MARKERS):
        raise ValueError(f"{name}: overall_comment looks like a stub/probe, not a real comment — FABRICATION GATE")
    rows = sheet["rows"]
    if not isinstance(rows, list) or [int(row.get("row", -1)) for row in rows] != expected[name]:
        raise ValueError(f"{name}: graded rows do not match assigned rows")
    blank_ok = "Không có câu trả lời."
    source_questions: dict[int, dict[str, Any]] = {}
    if payload_batch:
        for src_sheet in payload_batch.get("sheets", []):
            if src_sheet["sheet_name"] == name:
                for q in src_sheet.get("questions", []):
                    source_questions[int(q["row"])] = q
    for row in rows:
        require_keys(row, ("row", "feedback", "score"), "graded row")
        feedback = str(row["feedback"])
        if not feedback.strip():
            raise ValueError(f"{name} row {row['row']}: feedback is empty")
        if strict and any(marker in feedback.lower() for marker in _STUB_MARKERS):
            raise ValueError(f"{name} row {row['row']}: feedback looks like a stub/probe — FABRICATION GATE")
        score = float(row["score"])
        if not 0 <= score <= 10:
            raise ValueError(f"{name} row {row['row']}: score outside 0..10")
        if strict and score == 0:
            src_q = source_questions.get(int(row["row"]))
            if (src_q is None or not str(src_q.get("answer", "")).strip()) and feedback.strip() != blank_ok:
                raise ValueError(f"{name} row {row['row']}: payload answer is EMPTY so score 0 requires exactly '{blank_ok}' (got '{feedback[:60]}…') — BLANK-INTEGRITY GATE")
            if src_q is not None and len(str(src_q.get("answer", "")).strip()) >= 30 and feedback.strip() == blank_ok:
                raise ValueError(f"{name} row {row['row']}: payload HAS an answer, grading it 'Không có câu trả lời.'/0 is a fabricated skip — FABRICATION GATE")


def command_validate_own_sheet(args: argparse.Namespace) -> None:
    """Gate-protected validation for a SINGLE grader to check its own mirror.

    Enforces per-grader ownership + fabrication gates so parallel graders can
    never influence or stub-race each other's mirror files.
    """
    batch = load_json(Path(args.batch))
    mirror_map = {m["sheet_name"]: m["file"] for m in batch.get("mirror_files", [])}
    if args.sheet not in mirror_map:
        raise ValueError(f"sheet not assigned in this batch: {args.sheet}")
    mirror_path = Path(mirror_map[args.sheet])
    if not mirror_path.is_file():
        raise ValueError(f"mirror file missing: {mirror_path}")
    data = load_json(mirror_path)
    if int(data.get("batch_id", -1)) != int(batch["batch_id"]):
        raise ValueError("mirror batch_id does not match batch payload")
    sheets = data.get("sheets", [])
    if len(sheets) != 1 or sheets[0].get("sheet_name") != args.sheet:
        raise ValueError(f"mirror must contain exactly sheet '{args.sheet}' (ownership gate)")
    expected = expected_sheets(batch)
    validate_single_sheet(expected, sheets[0], payload_batch=batch, strict=True)
    print(json.dumps({"valid": True, "sheet_name": args.sheet, "sheet_count": 1}, ensure_ascii=False))


def validate_partial(batch: dict[str, Any], partial: dict[str, Any]) -> None:
    require_keys(partial, ("batch_id", "sheets"), "partial")
    if int(partial["batch_id"]) != int(batch["batch_id"]):
        raise ValueError("partial batch_id does not match batch payload")
    expected = expected_sheets(batch)
    actual = partial["sheets"]
    if not isinstance(actual, list) or [sheet.get("sheet_name") for sheet in actual if isinstance(sheet, dict)] != list(expected):
        raise ValueError("partial sheets must exactly match assigned batch order")
    for sheet in actual:
        validate_single_sheet(expected, sheet)


def command_validate_partial(args: argparse.Namespace) -> None:
    batch, partial_file_arg = load_json(Path(args.batch)), Path(args.partial)
    # Step 1: mirrors are the source of truth during parallel grading. Each
    # must hold exactly one valid sheet. The shared partial is checked AFTER.
    sheets = validate_mirror_files(batch, no_shared=True)
    partial_path = batch.get("partial_file")
    if not partial_path:
        raise ValueError("batch payload has no partial_file reference")
    if not partial_file_arg.is_file():
        # Shared partial missing: build it from validated mirrors.
        save_json(partial_file_arg, {"batch_id": batch["batch_id"], "sheets": sheets})
    else:
        p = load_json(partial_file_arg)
        if int(p.get("batch_id", -1)) != int(batch["batch_id"]) or [s.get("sheet_name") for s in p.get("sheets", [])] != batch["sheet_names"]:
            # Wrong shape/order — symptoms of a racing lost-update: heal it
            # from the mirrors (the source of truth).
            save_json(partial_file_arg, {"batch_id": batch["batch_id"], "sheets": sheets})
        else:
            # If a mirror sheet's feedback/scores were corrected, sync the shared file.
            sig = lambda s: tuple((r.get("row"), r.get("feedback"), r.get("score")) for r in s.get("rows", []))
            p_by = {s.get("sheet_name"): s for s in p.get("sheets", [])}
            for sh in sheets:
                if sig(p_by.get(sh["sheet_name"], {})) != sig(sh):
                    save_json(partial_file_arg, {"batch_id": batch["batch_id"], "sheets": sheets})
                    break
    print(json.dumps({"valid": True, "batch_id": batch["batch_id"], "sheet_count": len(batch.get("sheet_names", []))}, ensure_ascii=False))


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
        try:
            partial = load_json(Path(item["partial_file"]))
            validate_partial(batch, partial)
        except (OSError, ValueError, json.JSONDecodeError):
            # Shared partial missing or corrupt (lost updates are common when
            # graders append concurrently). Reconstruct from mirror files that
            # should each hold exactly one sheet.
            sheets = validate_mirror_files(batch, no_shared=True)
            partial = {"batch_id": batch["batch_id"], "sheets": sheets}
            save_json(Path(item["partial_file"]), partial)
        else:
            # Detect a racing lost-update: shared partial must match mirrors.
            try:
                validate_mirror_files(batch, no_shared=False)
            except ValueError:
                sheets = validate_mirror_files(batch, no_shared=True)
                partial = {"batch_id": batch["batch_id"], "sheets": sheets}
                save_json(Path(item["partial_file"]), partial)
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
    own = commands.add_parser("validate-own-sheet")
    own.add_argument("--batch", required=True); own.add_argument("--sheet", required=True); own.set_defaults(func=command_validate_own_sheet)
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
