---
name: grade-excel
description: Chấm workbook Excel nhiều sheet theo rubric kỹ thuật, ghi feedback/score vào cột J/K, nhận xét chung ở dòng tổng kết và công thức tổng điểm tự động.
---

# /grade-excel

Dùng skill này khi bạn muốn chấm một file `.xlsx` có cấu trúc theo `references/workbook-contract.md`.

## Cách gọi
- `/grade-excel results-7.xlsx`
- `/grade-excel results-7.xlsx --sheet thuylinh04204`

## Thành phần nội bộ của skill
- `scripts/preflight.py`: kiểm tra môi trường Python và đảm bảo `openpyxl` đã sẵn sàng.
- `scripts/grade_excel.py`: source thực thi để export/apply/verify workbook.
- `assets/grade_excel_prompt.md`: prompt contract buộc AI trả JSON grading hợp lệ.
- `references/workbook-contract.md`: tài liệu tham chiếu về layout workbook, scoring rules, và writeback rules.

## Workflow bắt buộc
1. Xác minh file workbook tồn tại.
2. Chạy preflight trước mọi bước khác:
   - `python ".claude/skills/grade-excel/scripts/preflight.py" ensure`
   - Nếu command này báo lỗi do không có Python hoặc không cài được package, dừng lại và báo rõ lỗi.
3. Export payload JSON bằng script của chính skill:
   - `python ".claude/skills/grade-excel/scripts/grade_excel.py" export --input <file>`
   - hoặc thêm `--sheet <name>` nếu chỉ chấm một sheet.
4. Đọc `assets/grade_excel_prompt.md` để lấy contract chấm bài.
5. Chấm dữ liệu workbook và tạo JSON grading theo đúng schema trong prompt.
6. Nếu cần, ghi JSON grading tạm ra file trong thư mục hiện tại.
7. Apply kết quả bằng script của chính skill:
   - `python ".claude/skills/grade-excel/scripts/grade_excel.py" apply --input <file> --grading <json>`
8. Verify key cells sau khi ghi:
   - `python ".claude/skills/grade-excel/scripts/grade_excel.py" verify --input <file>`
9. Báo lại sheet nào đã được chấm và file nào đã được cập nhật.

## Quy tắc chấm
- Feedback phải bằng tiếng Việt.
- Mỗi câu chấm 0-10.
- Must-have tối đa 8, nice-to-have tối đa 2, optional là bonus nhưng trần vẫn 10.
- Với câu bỏ trống, feedback phải nêu rõ là không có câu trả lời và cho 0 điểm.
- Dải câu hỏi được suy ra động từ sheet, không giả định luôn có đúng 10 câu.
- Ô tổng công thức không do AI tính; script luôn tự ghi công thức `=SUM(K<start>:K<end>)` theo dải câu hỏi phát hiện được.

## Không làm
- Không đổi cấu trúc workbook ngoài các ô feedback/score của dải câu hỏi và hai ô tổng kết của sheet.
- Không trả output text tự do để apply; phải dùng JSON đúng schema.
- Không phụ thuộc vào source code hay prompt nằm ngoài folder skill này khi chạy workflow chính.
