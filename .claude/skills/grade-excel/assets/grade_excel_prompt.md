# Grading Prompt (Compact)

## Task
Đánh giá câu trả lời của sinh viên và trả về JSON grading cho ĐÚNG MỘT học viên.

## Input
- Batch payload được giao (`batch-<NNN>-payload.json`) — CHỈ chấm sheet (học viên) được giao trong task.
- Bỏ qua các học viên khác trong cùng batch payload.
- Ghi KẾT QUẢ vào file partial riêng của mình (đường dẫn do agent chính giao — dạng `partial-<NNN>-<sheet>-grading.json`, xem `mirror_files` trong manifest) — KHÔNG sửa file partial chung, KHÔNG ghi vào file của grader khác.

## Rubric
- Must-have: max 8 điểm
- Nice-to-have: max 2 điểm
- Optional: max 1 bonus điểm
- **Cap: 10 điểm/câu**
- Bỏ trống → feedback "Không có câu trả lời." + score 0

## Output Format (partial JSON của subagent)
```json
{
  "batch_id": 1,
  "sheets": [
    {
      "sheet_name": "<tên>",
      "rows": [{"row": N, "feedback": "...", "score": N}, ...],
      "overall_comment": "..."
    }
  ]
}
```

Chỉ trả về đúng 1 sheet được giao trong batch inside `sheets` list. Agent chính sẽ validate, gộp partial JSON, bổ sung `raw_score`, `total_score` (thang 10), `max_score`, `percentage`, `answered_count`, `question_count` và `class_summary` vào file grading JSON cuối, đồng thời tạo Markdown report. Không tạo công thức Excel và không thêm tham chiếu ô J/K.

## Scoring Guide
- 8-10: Đầy đủ must-have + nice-to-have (có thể có optional)
- 5-7: Đạt must-have cơ bản
- 3-4: Thiếu nhiều nội dung must-have
- 0-2: Sai hoặc không có câu trả lời

## Overall Comment cần có
- Kiến thức tốt
- Kiến thức cần cải thiện
- Mức độ hoàn chỉnh
- Hướng cải thiện ngắn gọn
