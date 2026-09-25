# Workbook contract

## Sheet layout assumptions
- Each sheet represents one student.
- Question rows begin at row 2.
- The number of questions is dynamic and is inferred by reading consecutive populated question rows starting from row 2.
- A question row is considered present while at least one of these cells is non-empty:
  - column A (`ID`)
  - column E (`Question`)
- The summary row is always the row immediately after the last detected question row.

## Input columns
- A: ID
- B: Type
- C: Level
- D: Module
- E: Question
- F: Answer
- G: Rubric Must-have
- H: Rubric Nice-to-have
- I: Rubric Optional

## Output columns
- Column J stores AI feedback for each detected question row.
- Column K stores AI scores for each detected question row.
- `J(summary_row)` stores the overall comment for the sheet.
- `K(summary_row)` must always contain `=SUM(K<start>:K<end>)`, where `<start>` and `<end>` are the detected question row bounds.

## Scoring rules
- Must-have contributes up to 8 points.
- Nice-to-have contributes up to 2 points.
- Optional can contribute bonus points, but the per-question score cap remains 10.
- Empty answers should receive explicit feedback and a score of 0.

## Required grading payload shape
Each graded sheet must include:
- exactly one graded row for each detected question row
- contiguous `row` values from `question_start_row` to `question_end_row`
- non-empty feedback for every row
- a numeric score between 0 and 10 for every row
- a non-empty `overall_comment`

## Writeback rules
When applying results back to the workbook:
- write row feedback into `J<question_start_row>:J<question_end_row>`
- write row scores into `K<question_start_row>:K<question_end_row>`
- write the overall comment into `J(summary_row)`
- overwrite `K(summary_row)` with `=SUM(K<question_start_row>:K<question_end_row>)`
