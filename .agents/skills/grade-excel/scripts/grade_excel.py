from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

QUESTION_START_ROW = 2
HEADERS = {
    "A": "ID",
    "B": "Type",
    "C": "Level",
    "D": "Module",
    "E": "Question",
    "F": "Answer",
    "G": "Rubric Must-have",
    "H": "Rubric Nice-to-have",
    "I": "Rubric Optional",
    "J": "AI Feedback",
    "K": "AI Score",
}


@dataclass(slots=True)
class GradedRow:
    row: int
    feedback: str
    score: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GradedRow":
        row = int(data["row"])
        feedback = str(data["feedback"]).strip()
        score = float(data["score"])
        if row < QUESTION_START_ROW:
            raise ValueError(f"Row must be >= {QUESTION_START_ROW}, got {row}")
        if not feedback:
            raise ValueError(f"Feedback must not be empty for row {row}")
        if score < 0 or score > 10:
            raise ValueError(f"Score must be between 0 and 10 for row {row}, got {score}")
        return cls(row=row, feedback=feedback, score=score)


@dataclass(slots=True)
class SheetGrade:
    sheet_name: str
    rows: list[GradedRow]
    overall_comment: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SheetGrade":
        sheet_name = str(data["sheet_name"])
        rows = [GradedRow.from_dict(row) for row in data["rows"]]
        overall_comment = str(data["overall_comment"]).strip()

        if not rows:
            raise ValueError(f"Sheet {sheet_name} must contain at least one graded row")
        if not overall_comment:
            raise ValueError(f"overall_comment must not be empty for sheet {sheet_name}")

        row_numbers = sorted(row.row for row in rows)
        expected_rows = list(range(row_numbers[0], row_numbers[-1] + 1))
        if row_numbers != expected_rows:
            raise ValueError(f"Rows must be contiguous, got {row_numbers}")

        return cls(sheet_name=sheet_name, rows=rows, overall_comment=overall_comment)

    @property
    def question_start_row(self) -> int:
        return min(row.row for row in self.rows)

    @property
    def question_end_row(self) -> int:
        return max(row.row for row in self.rows)

    @property
    def question_rows(self) -> list[int]:
        return [row.row for row in sorted(self.rows, key=lambda item: item.row)]

    @property
    def summary_row(self) -> int:
        return self.question_end_row + 1

    @property
    def feedback_range(self) -> str:
        return f"J{self.question_start_row}:J{self.question_end_row}"

    @property
    def score_range(self) -> str:
        return f"K{self.question_start_row}:K{self.question_end_row}"

    @property
    def overall_comment_cell(self) -> str:
        return f"J{self.summary_row}"

    @property
    def total_formula_cell(self) -> str:
        return f"K{self.summary_row}"

    @property
    def total_formula(self) -> str:
        return f"=SUM(K{self.question_start_row}:K{self.question_end_row})"


def _normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _question_rows(ws) -> list[int]:
    rows: list[int] = []
    row = QUESTION_START_ROW
    while True:
        question = _normalize_cell(ws[f"E{row}"].value)
        question_id = _normalize_cell(ws[f"A{row}"].value)
        if not question and not question_id:
            break
        rows.append(row)
        row += 1
    if not rows:
        raise ValueError(f"No question rows found in sheet {ws.title} starting at row {QUESTION_START_ROW}")
    return rows


def _sheet_layout(ws) -> dict[str, Any]:
    question_rows = _question_rows(ws)
    question_start_row = question_rows[0]
    question_end_row = question_rows[-1]
    summary_row = question_end_row + 1
    return {
        "question_rows": question_rows,
        "question_start_row": question_start_row,
        "question_end_row": question_end_row,
        "summary_row": summary_row,
        "feedback_range": f"J{question_start_row}:J{question_end_row}",
        "score_range": f"K{question_start_row}:K{question_end_row}",
        "overall_comment_cell": f"J{summary_row}",
        "total_formula_cell": f"K{summary_row}",
        "total_formula": f"=SUM(K{question_start_row}:K{question_end_row})",
    }


def _sheet_payload(ws) -> dict[str, Any]:
    layout = _sheet_layout(ws)
    questions: list[dict[str, Any]] = []
    for row in layout["question_rows"]:
        questions.append(
            {
                "row": row,
                "id": _normalize_cell(ws[f"A{row}"].value),
                "type": _normalize_cell(ws[f"B{row}"].value),
                "level": _normalize_cell(ws[f"C{row}"].value),
                "module": _normalize_cell(ws[f"D{row}"].value),
                "question": _normalize_cell(ws[f"E{row}"].value),
                "answer": _normalize_cell(ws[f"F{row}"].value),
                "rubric_must_have": _normalize_cell(ws[f"G{row}"].value),
                "rubric_nice_to_have": _normalize_cell(ws[f"H{row}"].value),
                "rubric_optional": _normalize_cell(ws[f"I{row}"].value),
            }
        )

    return {
        "sheet_name": ws.title,
        "question_start_row": layout["question_start_row"],
        "question_end_row": layout["question_end_row"],
        "summary_row": layout["summary_row"],
        "questions": questions,
        "output_contract": {
            "feedback_range": layout["feedback_range"],
            "score_range": layout["score_range"],
            "overall_comment_cell": layout["overall_comment_cell"],
            "total_formula_cell": layout["total_formula_cell"],
            "total_formula": layout["total_formula"],
        },
    }


def export_workbook_payload(input_path: str | Path, sheet_name: str | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    workbook = load_workbook(filename=Path(input_path))
    sheets = workbook.sheetnames if sheet_name is None else [sheet_name]
    payload_sheets = []

    for current_sheet in sheets:
        if current_sheet not in workbook.sheetnames:
            raise ValueError(f"Sheet not found: {current_sheet}")
        payload_sheets.append(_sheet_payload(workbook[current_sheet]))

    payload = {
        "workbook_path": str(Path(input_path)),
        "headers": HEADERS,
        "rubric": {
            "must_have_max": 8,
            "nice_to_have_max": 2,
            "optional_bonus": 1,
            "score_cap": 10,
        },
        "sheets": payload_sheets,
    }

    if output_dir is not None:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload_path = out_dir / f"{Path(input_path).stem}_payload.json"
        with payload_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return payload_path

    return payload


def load_grading_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def _load_sheet_grades(grading_data: dict[str, Any]) -> list[SheetGrade]:
    if "sheets" not in grading_data:
        raise ValueError("Grading payload must contain a 'sheets' key")
    sheets = [SheetGrade.from_dict(item) for item in grading_data["sheets"]]
    if not sheets:
        raise ValueError("Grading payload must contain at least one sheet result")
    return sheets


def apply_grading_results(
    input_path: str | Path,
    grading_data: dict[str, Any],
    output_path: str | Path | None = None,
) -> Path:
    workbook_path = Path(input_path)
    save_path = Path(output_path) if output_path else workbook_path
    workbook = load_workbook(filename=workbook_path)

    for sheet_grade in _load_sheet_grades(grading_data):
        if sheet_grade.sheet_name not in workbook.sheetnames:
            raise ValueError(f"Sheet not found in workbook: {sheet_grade.sheet_name}")
        worksheet = workbook[sheet_grade.sheet_name]
        for row_grade in sheet_grade.rows:
            worksheet[f"J{row_grade.row}"] = row_grade.feedback
            worksheet[f"K{row_grade.row}"] = row_grade.score
        worksheet[sheet_grade.overall_comment_cell] = sheet_grade.overall_comment
        worksheet[sheet_grade.total_formula_cell] = sheet_grade.total_formula

    workbook.save(save_path)
    return save_path


def verify_workbook(input_path: str | Path, sheet_name: str | None = None) -> dict[str, Any]:
    workbook = load_workbook(filename=Path(input_path), data_only=False)
    sheets = workbook.sheetnames if sheet_name is None else [sheet_name]
    summary = []

    for current_sheet in sheets:
        if current_sheet not in workbook.sheetnames:
            raise ValueError(f"Sheet not found: {current_sheet}")
        ws = workbook[current_sheet]
        layout = _sheet_layout(ws)
        summary.append(
            {
                "sheet_name": current_sheet,
                "question_start_row": layout["question_start_row"],
                "question_end_row": layout["question_end_row"],
                "summary_row": layout["summary_row"],
                "J_summary": ws[layout["overall_comment_cell"]].value,
                "K_summary": ws[layout["total_formula_cell"]].value,
                "first_score": ws[f"K{layout['question_start_row']}"] .value,
                "last_score": ws[f"K{layout['question_end_row']}"] .value,
            }
        )

    return {"sheets": summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export workbook grading payloads, apply grading results, and verify output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export workbook payload as JSON")
    export_parser.add_argument("--input", required=True, help="Path to input .xlsx workbook")
    export_parser.add_argument("--sheet", help="Optional single sheet name to export")
    export_parser.add_argument("--output-dir", help="Optional directory to save payload JSON file")

    apply_parser = subparsers.add_parser("apply", help="Apply grading JSON to workbook")
    apply_parser.add_argument("--input", required=True, help="Path to input .xlsx workbook")
    apply_parser.add_argument("--grading", required=True, help="Path to grading JSON file")
    apply_parser.add_argument("--output", help="Optional output workbook path")

    verify_parser = subparsers.add_parser("verify", help="Verify key workbook cells after apply")
    verify_parser.add_argument("--input", required=True, help="Path to input .xlsx workbook")
    verify_parser.add_argument("--sheet", help="Optional single sheet name to verify")

    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "export":
        result = export_workbook_payload(input_path=args.input, sheet_name=args.sheet, output_dir=args.output_dir)
        if isinstance(result, Path):
            print(str(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply":
        grading_data = load_grading_json(args.grading)
        output_path = apply_grading_results(input_path=args.input, grading_data=grading_data, output_path=args.output)
        print(str(Path(output_path)))
        return 0

    if args.command == "verify":
        print(json.dumps(verify_workbook(input_path=args.input, sheet_name=args.sheet), ensure_ascii=False, indent=2))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
