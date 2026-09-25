# Ví dụ dùng /grade-excel

## Chấm toàn bộ workbook
`/grade-excel results-7.xlsx`

## Chấm một sheet cụ thể
`/grade-excel results-7.xlsx --sheet thuylinh04204`

## Export payload thủ công
`python .claude/skills/grade-excel/scripts/grade_excel.py export --input results-7.xlsx`

## Apply grading JSON thủ công
`python .claude/skills/grade-excel/scripts/grade_excel.py apply --input results-7.xlsx --grading grading.json`
