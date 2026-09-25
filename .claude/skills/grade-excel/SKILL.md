---
name: grade-excel
description: Chấm payload JSON bài làm theo rubric kỹ thuật và xuất grading JSON với điểm thành phần, tổng điểm, nhận xét và thống kê.
---

# /grade-excel

Dùng skill này khi bạn đã có file payload `.json` theo schema export của `grade-excel` và muốn chấm bài. Input và output đều là JSON; skill không đọc, tạo, ghi hoặc verify file Excel.

## Cách gọi
- `/grade-excel results-102-grade-excel-payload.json`
- `/grade-excel results-102-grade-excel-payload.json --sheet AnhTP43`
- `/grade-excel results-102-grade-excel-payload.json --output results-102-grading.json`

Nếu không truyền `--output`, tạo hai file cùng thư mục với payload: `<payload-stem>-grading.json` và `<payload-stem>-report.md`. Trường `workbook_path` trong payload cũ là metadata tùy chọn và bị bỏ qua.

## Mục tiêu vận hành
Sau khi user gọi skill, skill tự chấm payload và xuất grading JSON kèm báo cáo Markdown tổng hợp. User không phải export lại payload, chạy command trung gian hoặc có file Excel.

## Workflow bắt buộc
1. Xác minh file payload JSON tồn tại và parse được. Không gọi lệnh `export`, không đọc Excel và không yêu cầu workbook.
2. Kiểm tra payload có `rubric`, `sheets`; mỗi sheet có `sheet_name`, `question_start_row`, `question_end_row`, `questions`; mỗi question có `row`, `answer` và ba rubric. Kiểm tra `row` liên tục trong dải câu hỏi. Các trường `workbook_path`, `headers`, `summary_row`, `output_contract` được chấp nhận nếu có, nhưng không dùng để tạo output. Payload sai thì dừng, nêu rõ lỗi.
3. Nếu có `--sheet`, chỉ lấy sheet đó từ payload; nếu không tìm thấy, dừng với lỗi rõ ràng. Nếu không có `--sheet`, dùng toàn bộ `sheets` của payload.
4. Chạy Python pipeline để thực hiện phần xác định được: validate payload, dọn `./.tmp`, chọn sheet, chia batch 5 học viên và tạo manifest/partial path. Không dùng LLM cho các bước này:
   - `python "<skill-root>/scripts/json_pipeline.py" prepare --input <payload> --project-dir <current-project> [--sheet <name>]`
5. Đọc `assets/grade_excel_prompt.md`. Chạy batch **tuần tự**: xử lý xong batch 1 (đã pass `validate-partial`) mới sang batch 2. Trong MỘT batch, spawn **5 grader subagent song song — mỗi grader chấm đúng 1 học viên (một sheet, 12 câu)** trong batch. Grader đọc batch payload lấy đúng sheet của mình, chấm theo rubric và ghi sheet của mình vào `batch-<NNN>-grading.json`: grader với sheet đầu tiên tạo file `{"batch_id": N, "sheets": [<sheet của mình>]}`, các grader khác đọc file hiện tại, append sheet của mình, ghi lại. Đây là bước duy nhất dùng LLM để tạo score/feedback. Không gom cả batch chấm một lần; mỗi grader chỉ giữ một sheet.
6. Sau khi 5 grader của batch hoàn tất, chạy `validate-partial` cho batch đó:
   - `python "<skill-root>/scripts/json_pipeline.py" validate-partial --batch <batch-payload> --partial <partial-json>`
   File partial cuối phải chứa ĐỦ các sheet của batch theo đúng thứ tự. Thiếu batch, sheet/row sai, feedback rỗng hoặc score ngoài 0–10 là lỗi cấu trúc; spawn lại grader cho đúng sheet lỗi trước review. Batch pass mới sang batch tiếp theo.
7. Validate batch xong review batch: spawn một **reviewer subagent độc lập** cho batch vừa pass. Reviewer không phải grader của batch đó; đọc `assets/grade_excel_review_prompt.md`, nhận batch payload và partial JSON, tự đối chiếu answer/rubric rồi ghi `batch-<NNN>-review-round-1.json` với verdict `pass` hoặc `changes_required`.
8. Dùng Python `validate-review` cho review vừa có:
   - `python "<skill-root>/scripts/json_pipeline.py" validate-review --batch <batch-payload> --review <review-json>`
   Nếu tất cả `pass`, dừng review ngay ở round 1. Nếu có `changes_required`, dispatch correction subagent cho đúng batch lỗi để cập nhật partial JSON theo findings, rồi validate lại partial.
9. Chỉ với các batch đã sửa, spawn reviewer độc lập mới cho **round 2** và validate review. Nếu pass, tiếp tục merge. Nếu round 2 vẫn còn finding: **apply thêm một vòng correction cuối** theo findings, validate lại partial, rồi merge (không review round 3). Correction cuối phải áp đúng recommended_score/feedback của reviewer; nếu bổ sung làm partial sai schema, dừng và báo lỗi.
10. Sau khi mọi batch pass review, chạy Python `merge` để gộp theo thứ tự payload, tính `raw_score`, `total_score` thang 10, thống kê, và tạo đồng thời grading JSON/Markdown:
   - `python "<skill-root>/scripts/json_pipeline.py" merge --manifest <manifest> --output <grading-json> --report <report-md>`
   Không dùng LLM để cộng điểm, xếp hạng hay render report.
11. Đọc lại JSON/Markdown cuối và dùng Python kiểm tra số sheet/row, score, điểm tổng và bảng report khớp dữ liệu JSON:
   - `python "<skill-root>/scripts/json_pipeline.py" verify-final --manifest <manifest> --output <grading-json> --report <report-md>`
   Báo đường dẫn hai file và bảng xếp hạng theo `total_score`.

## Grading JSON đầu ra

Output phải có shape sau; không chứa công thức Excel:

```json
{
  "source_payload": "results-102-grade-excel-payload.json",
  "rubric": {"must_have_max": 8, "nice_to_have_max": 2, "optional_bonus": 1, "score_cap": 10},
  "sheets": [
    {
      "sheet_name": "AnhTP43",
      "rows": [{"row": 2, "feedback": "...", "score": 0}],
      "overall_comment": "...",
      "raw_score": 0,
      "total_score": 0.0,
      "max_score": 30,
      "percentage": 0.0,
      "answered_count": 0,
      "question_count": 3
    }
  ],
  "class_summary": {"raw_score": 0, "total_score": 0.0, "max_score": 30, "percentage": 0.0, "answered_count": 0, "question_count": 3}
}
```

## Markdown report đầu ra

File `<payload-stem>-report.md` phải có tiêu đề, nguồn payload, tổng lớp (`raw_score/max_score`, `total_score/10`, số câu có trả lời/tổng số câu) và bảng xếp hạng giảm dần gồm: hạng, sheet/học viên, điểm tổng `/10`, điểm thô, số câu có trả lời, nhận xét tổng quan. Dữ liệu trong Markdown phải khớp grading JSON.

## Partial JSON của subagent

Mỗi file `batch-<NNN>-grading.json` chỉ chứa kết quả chấm trung gian:

```json
{"batch_id": 1, "sheets": [{"sheet_name": "AnhTP43", "rows": [{"row": 2, "feedback": "...", "score": 0}], "overall_comment": "..."}]}
```

Agent chính là nơi duy nhất gộp partial JSON, tính tổng và tạo hai file output cuối.

## Review JSON độc lập

Reviewer chỉ đánh giá partial JSON, không ghi điểm trực tiếp. Mỗi batch/round phải có một file:

```json
{"batch_id": 1, "verdict": "changes_required", "findings": [{"sheet_name": "AnhTP43", "row": 2, "issue": "Lý do cần điều chỉnh", "recommended_score": 4}]}
```

`pass` bắt buộc có `findings: []`. Reviewer phải chấm độc lập từ answer/rubric, không chỉ kiểm tra JSON schema. Tối đa hai round: pass round 1 thì dừng; có finding round 1 thì sửa và review lại một lần; còn finding round 2 thì fail closed.

## Báo cáo thống kê học viên

Tạo từ grading JSON cuối cùng, không từ Excel:

- Mỗi câu nằm trong dải đã xác thực từ payload (`question_start_row`..`question_end_row`).
- **Tổng số câu làm được** = `answered_count`: số câu có câu trả lời khác rỗng trong payload; câu trả lời đạt 0 vẫn được tính. Ví dụ: `11/14`.
- **Điểm tổng (thang 10)** = `total_score` = `round(raw_score / question_count, 2)`. Ví dụ: `110 điểm / 14 câu = 7.86/10`.
- **Điểm thô** = `raw_score` trên `max_score`, để đối soát tổng điểm thành phần.
- Dải câu hỏi động theo từng sheet lấy từ payload; nếu sheet có `N` câu thì mẫu tổng điểm là `/N×10`.
- Xuất bảng sắp theo tổng điểm giảm dần, kèm hàng tổng (tổng số câu làm được / tổng số câu, tổng điểm / tổng điểm tối đa, trung bình toàn lớp).

## Quy tắc chấm
- Feedback phải bằng tiếng Việt.
- Mỗi câu chấm 0-10.
- Must-have tối đa 8, nice-to-have tối đa 2, optional là bonus nhưng trần vẫn 10.
- Với câu bỏ trống, feedback phải nêu rõ là không có câu trả lời và cho 0 điểm.
- Dải câu hỏi được lấy từ payload đã xác thực, không giả định luôn có đúng 10 câu.
- `total_score` luôn là trung bình điểm từng câu trên thang 10, làm tròn 2 chữ số; không dùng công thức Excel.

## Thành phần nội bộ

- `scripts/json_pipeline.py`: validate, dọn `.tmp`, chia batch, validate partial/review, merge, tính điểm và tạo Markdown bằng Python stdlib.
- `assets/grade_excel_prompt.md`: contract LLM cho grader batch.
- `assets/grade_excel_review_prompt.md`: contract LLM cho reviewer độc lập.
- `./.tmp/grade-excel/<payload-stem>/`: manifest, batch payload, partial JSON và review JSON của lần chạy hiện tại.

## Thư mục tạm (`.tmp`)

Partial JSON luôn nằm trong `.tmp` của project hiện tại, không nằm trong folder skill: `./.tmp/grade-excel/<payload-stem>/`. Có thể xóa thư mục batch sau khi đã kiểm tra output cuối.

Mỗi lần chạy mới, skill dọn sạch toàn bộ nội dung `.tmp` trước khi tạo batch mới; partial JSON của lần chạy trước không được tái sử dụng.

## Không làm
- Không đọc, tạo, ghi hoặc verify workbook Excel.
- Không gọi `grade_excel.py`, `parallel_grade.py` hoặc preflight trong workflow này.
- Không trả output text tự do: phải tạo grading JSON đúng schema và ghi ra file.
- Không dùng `workbook_path`, `summary_row` hoặc `output_contract` để quyết định kết quả.
- Batch xử lý tuần tự (batch sau chỉ chạy khi batch trước pass validate+review); trong batch, phải spawn 5 grader subagent song song (mỗi grader 1 học viên), không cho 1 grader chấm cả batch.
- Không để subagent ghi đè partial của batch khác hoặc các file output cuối.
- Không xóa chính thư mục `.tmp` hoặc bất kỳ file/thư mục nào nằm ngoài `.tmp` của project hiện tại.
- Không để grader tự tính tổng, render report, validate schema hoặc tự phê duyệt kết quả của chính nó.
- Không merge nếu có partial/review lỗi, hoặc correction cuối (sau round 2) không validate được. Review tối đa 2 round; có finding ở round 2 thì apply correction cuối theo reviewer rồi merge, không review round 3.
- Không chạy reviewer round 2 khi mọi batch đã pass round 1, và không chạy review quá 2 round.
- Không phụ thuộc source code hay prompt ngoài folder skill khi chạy workflow chính.
