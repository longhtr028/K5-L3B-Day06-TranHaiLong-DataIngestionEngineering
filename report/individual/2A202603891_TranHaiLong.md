# Báo cáo cá nhân — Day 6

**Trần Hải Long · 2A202603891** · Chạy ngày 13/11/2026 trên MacBook (CPU)

## Dữ liệu và pipeline

- **Dữ liệu:** Luật Sở hữu trí tuệ, bản hợp nhất 2026, 1 file PDF 140 trang (`data/raw/luat-shtt-hn-2026.pdf`).
  - **Quyền:** văn bản luật không được bảo hộ quyền tác giả (Điều 15 khoản 2 của chính Luật này), nên được phép dùng và chia sẻ.
  - **Số trang:** tính 137 trang nội dung, tức là các trang có ít nhất 500 ký tự. Bỏ trang 1 (bìa và danh sách luật sửa đổi; vẫn được nạp) và trang 137–138 (chỉ có chú thích). Chi tiết từng trang: `data/manifest_pages.csv`.
- **Đọc và làm sạch:**
  - Dùng `pypdf`, chỉ giữ chữ cỡ ≥13pt. Thân bài cỡ 14pt; chú thích cuối trang cỡ 12pt bị bỏ. Nếu không bỏ, số chú thích dính vào chữ, ví dụ "thân gỗ300".
  - Chuẩn hóa Unicode (NFC) và khoảng trắng, giữ xuống dòng để nhận ra khoản/điểm.
  - Lỗi ghi lại: 2 trang không có thân bài (`starter/results/ingest_errors.csv`).
  - Giới hạn: một số chữ bị PDF tách sai ("liên h ệ"), và phần chú thích (lịch sử sửa đổi) không được nạp.
- **Model và kho vector:**
  - Embedding: `intfloat/multilingual-e5-small`, chạy trên máy, 384 chiều, chi phí 0 đồng. Không chunk nào vượt giới hạn 512 token.
  - Kho vector: PostgreSQL 16 + pgvector 0.8.7, đo bằng cosine, tìm chính xác trên toàn bảng (không tìm gần đúng).
  - Lệnh chạy: xem phần "Bài làm" trong README.
- **Metadata của mỗi chunk:**
  - `document_id`, `source`, `page`;
  - `part`: Phần của Luật, 1–6. Đây là trường dùng để lọc;
  - `chapter`, `articles`: Chương và các Điều mà chunk chứa.
- **Vì sao lọc theo `part`:** Luật có nhiều điều gần giống nhau ở các Phần khác nhau. Ví dụ, Điều 93 (Phần 3, sáng chế/nhãn hiệu) và Điều 169 (Phần 4, giống cây trồng) đều nói "văn bằng bảo hộ có hiệu lực…".

## Cấu hình và benchmark

- **A:** cắt cứng mỗi 500 ký tự, chồng nhau 50 ký tự, không lọc. Ra 660 chunk.
- **B:** cắt theo đoạn (khoản/điểm), tối đa 800 ký tự; mỗi Điều luôn bắt đầu một chunk mới. Không lọc. Ra 532 chunk.
- **C:** giống hệt B, chỉ thêm lọc `part` cho những câu hỏi có ghi phạm vi.

So sánh A với B thấy tác động của cách cắt chunk. So sánh B với C thấy tác động của bộ lọc, vì chỉ khác đúng một yếu tố.

**Bộ câu hỏi:** 26 câu (`benchmark/questions.jsonl`), chia thành 19 dev và 7 test trước khi chạy.

- Các câu về cùng một Điều nằm chung một nhóm, để test không bị lộ qua dev.
- Có 6 câu cần lọc: 4 câu ở dev, 2 câu ở test (f01, f02).
- Cả 28 đoạn bằng chứng đều được kiểm tra tự động là có thật ở đúng trang (`benchmark/evidence_check.csv`).

**Quy tắc chọn chiến lược (đặt trước khi xem test):** chọn chiến lược có Hit@3 cao nhất trên dev. Nếu hòa, xét Hit@1, rồi số chunk ít hơn.

Kết quả chạy dev: C được 14/19, A 12/19, B 11/19, nên chọn C. Lựa chọn được ghi vào `artifacts/experiment_log.csv` trước khi chạy test. Test chỉ chạy một lần.

## Kết quả

| Chiến lược | Tập | Hit@1 | Hit@3 | Số chunk | Thời gian embed | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 9/19 | 12/19 | 660 | 8,7 s | 0 |
| A | test | 1/7 | 3/7 | | | |
| B | dev | 11/19 | 11/19 | 532 | 8,4 s | 0 |
| B | test | 4/7 | 5/7 | | | |
| C | dev | 13/19 | 14/19 | dùng chung B | 0 | 0 |
| C | test | 6/7 | 7/7 | | | |

| Chiến lược | Độ mạch lạc /2 (10 mẫu) | Metadata đúng | Đúng phạm vi lọc | Bằng chứng 0/1/2 (7 câu test) |
|---|---|---|---|---|
| A | 0,90 | 21/21 | 0/2 | 3/1/3, trung bình 1,00 |
| B | 1,90 | 21/21 | 0/2 | 2/0/5, trung bình 1,43 |
| C | 1,90 (chung chunk B) | 21/21 | 2/2 | 0/0/7, trung bình 2,00 |

**Cách đọc bảng:**

- **Hit:** chunk đúng trang và chứa đủ đoạn bằng chứng.
- **Độ mạch lạc** chấm 10 vị trí chọn trước: điểm giữa các trang 10, 20, …, 100.
- **Metadata** và **bằng chứng** được chấm trên cả 7 câu test, mỗi câu lấy 3 kết quả đầu.
- **Đúng phạm vi lọc** của A và B đo xem kết quả có tình cờ rơi đúng Phần không; hai chiến lược này không lọc.

**Nạp lại không trùng:** chạy ingest lần hai không sinh vector mới, số dòng giữ nguyên. Khi giả lập sửa 1 trang và xóa 1 trang, chỉ chunk bị sửa được tính lại và các chunk của trang bị xóa cũng bị xóa (`starter/results/reingest_check.json`).

**File kết quả:**

- Kết quả top-3 của từng câu: `starter/results/traces/traces_all.jsonl`
- Bảng số liệu: `starter/results/metrics_all.md`
- Nhãn chấm tay kèm lý do: `starter/results/audit/*_labels.csv`

## Phân tích và kết luận

**A so với B (đổi cách cắt):**

- B dễ đọc hơn hẳn: 1,90 so với 0,90. Chunk của A thường bị cắt giữa từ, có chỗ mất cả ý chính. Ví dụ, mẫu pos07 bị cắt đúng chỗ thời hạn "bảy tháng".
- B tốt hơn ở Hit@1 và ở toàn bộ tập test. Riêng Hit@3 trên dev, A hơn 1 câu, vì các đoạn chồng nhau của A cho nhiều "lần thử" trên cùng một Điều.
- **Lỗi 1 — A, q12 (trang 72):** đoạn cắt cứng chia đôi câu bằng chứng ("được coi như" / "đã rút"), nên kết quả đầu không đủ bằng chứng.
- **Lỗi 2 — B, q13 (trang 76):** Điều 119 dài hơn 800 ký tự nên bị chia làm hai. Phần có đáp án "năm tháng" không lọt vào top-3.

**B so với C (thêm lọc):**

- 20 câu không có phạm vi cho kết quả giống hệt nhau.
- Với 6 câu có phạm vi: Hit@3 tăng từ 1/6 lên 6/6.
- **Ví dụ f01:** không lọc, B trả về Điều 93 (sáng chế), dễ dẫn đến trả lời sai "20 năm". Lọc `part=4` thì C trả về đúng Điều 169 (giống cây trồng).

**Chọn C.**

- Điều kiện: người dùng phải biết trước mình hỏi về Phần nào.
- Rủi ro: lọc sai Phần sẽ mất đáp án; các quy định chung ở Phần 1 bị loại khi lọc theo Phần khác.
- B và C có ít chunk hơn A khoảng 19%.

**Hạn chế:**

- Tập test chỉ có 7 câu, nên chênh lệch 1–2 câu chưa có ý nghĩa thống kê. Kết luận chỉ đúng cho bộ luật này.
- 4 câu cả ba chiến lược đều trượt:
  - q09: câu bị ngắt sang trang sau;
  - q07, q08, q19: câu hỏi diễn đạt khác hẳn văn bản luật.
- Nhãn do một người chấm, không có người chấm thứ hai.
- Thời gian chỉ đo một lần, trên một máy.

**Nếu làm lại:**

1. Cho chunk nối qua trang trong cùng một Điều (sửa được q09, q12).
2. Thêm tên Điều vào đầu chunk.
3. Thử kết hợp tìm theo từ khóa (BM25) cho các câu diễn đạt khác.
4. Thử model embedding lớn hơn.

## Công cụ AI

- Code pipeline, các lần chạy và **bản nháp nhãn chấm tay** được làm bằng Claude Code. Mỗi nhãn có lý do, ghi trong cột `labeller` của file CSV.
- Những gì đã kiểm tra tự động:
  - cả 28 đoạn bằng chứng có đúng ở trang ghi trong gold;
  - metadata của 63 kết quả test khớp với văn bản gốc khi dò lại bằng một script riêng;
  - mỗi câu trượt đều đã được đọc lại trong file kết quả.
