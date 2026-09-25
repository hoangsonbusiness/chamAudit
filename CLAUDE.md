# CLAUDE.md

This repository grades technical-assessment payload JSON files. The current workflow is JSON-only: it does not open, create, update, or verify an Excel workbook.

## Primary entrypoint

Use the project-local Claude Code skill:

```text
/grade-excel results-102-grade-excel-payload.json
/grade-excel results-102-grade-excel-payload.json --sheet AnhTP43
```

The skill is located at `.claude/skills/grade-excel/`. It produces two files beside the input payload unless `--output` selects the JSON path:

- `<payload-stem>-grading.json`
- `<payload-stem>-report.md`

`workbook_path`, `headers`, `summary_row`, and `output_contract` in an input payload are accepted legacy metadata. They must not drive the grading result.

## Current workflow

1. Run `.claude/skills/grade-excel/scripts/json_pipeline.py prepare` to validate the payload, clear the contents of `./.tmp/`, select an optional sheet, and create batches of at most five students.
2. Spawn one grading subagent per batch and dispatch batches in parallel. Each grader writes only its own `batch-<NNN>-grading.json`.
3. Run `validate-partial` for every batch before review.
4. Spawn a separate reviewer subagent per batch. The reviewer independently evaluates the answers and rubrics, then writes `batch-<NNN>-review-round-1.json`.
5. Validate all reviews. When every review passes, merge immediately. For a finding in round 1, a correction agent updates only that batch and a new independent reviewer performs round 2. Any finding in round 2 stops the workflow without final output.
6. Run `merge` to calculate totals and write the JSON and Markdown outputs, then run `verify-final` to verify both files.

The batch and review files belong in:

```text
./.tmp/grade-excel/<payload-stem>/
```

Each run clears every file and child directory inside the project `.tmp` folder before creating a new manifest. Preserve `.tmp` itself and never delete outside the current project.

## Deterministic pipeline

`json_pipeline.py` uses only Python standard library and owns all work that does not require judgment:

| Command | Responsibility |
|---|---|
| `prepare` | Validate payload, clear `.tmp`, and create batch payloads and manifest |
| `validate-partial` | Validate assigned sheets, rows, feedback, and 0–10 scores |
| `validate-review` | Validate reviewer verdict and findings schema |
| `merge` | Merge passed partials, calculate totals, and render JSON/Markdown |
| `verify-final` | Recompute scores and check that Markdown matches final JSON |

Use the installed Python 3.11 when needed:

```text
C:\Users\hoang\AppData\Local\Programs\Python\Python311\python.exe
```

## LLM work and contracts

Only subagents perform judgment:

- `assets/grade_excel_prompt.md` defines batch grading output.
- `assets/grade_excel_review_prompt.md` defines independent review output.

Grader feedback must be Vietnamese. Each question score ranges from 0 to 10. Empty answers receive exactly `Không có câu trả lời.` and score 0.

The final JSON contains row feedback and scores, `raw_score`, and `total_score`. `total_score` is on a 10-point scale: `round(raw_score / question_count, 2)`. It also contains `max_score`, `percentage`, `answered_count`, `question_count`, and `class_summary`.

## Legacy files

`excel_grader/`, `prompts/grade_excel_sheet.md`, `scripts/grade_excel.py`, `scripts/parallel_grade.py`, `scripts/preflight.py`, and workbook-contract references remain for historical Excel workflows. Do not use them for the current payload JSON skill.
