# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm còn trống, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.

---

## Bài làm: cách chạy lại

Corpus: Luật Sở hữu trí tuệ (văn bản hợp nhất 2026), 140 trang PDF, 137 trang nội dung. Xem [data/manifest.json](data/manifest.json) và [data/manifest_pages.csv](data/manifest_pages.csv). Embedding: `intfloat/multilingual-e5-small` chạy local (384 chiều, chi phí 0). Store: PostgreSQL 16 + pgvector 0.8.7, cosine, exact search.

```bash
cd starter
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env            # chỉ cần DATABASE_URL; EMBEDDING_MODEL để trống hoặc = intfloat/multilingual-e5-small
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
python ../benchmark/verify_evidence.py        # kiểm chứng 28/28 evidence trước khi chạy
python -m src.run ingest                      # chạy 2 lần: lần 2 phải là 0 vector mới, số dòng không đổi
python scripts/check_reingest.py              # kiểm tra tài liệu đổi: chunk cũ bị xóa, chỉ embed lại chunk đổi
python -m src.run bench --split dev           # chọn chiến lược trên dev
python -m src.run bench                       # dev + test, ghi trace và metrics
python scripts/build_audit_samples.py         # tạo mẫu audit (không sinh nhãn)
python scripts/summarize_audit.py             # tổng hợp nhãn thủ công
```

| Thành phần | Đường dẫn |
|---|---|
| Code pipeline | `starter/src/` (ingestion, chunking, embeddings, store, evaluation, run) |
| Cấu hình A/B/C + quy tắc chọn | `starter/configs/strategies.json`, `artifacts/experiment_log.csv` |
| Benchmark + gold + split | `benchmark/questions.jsonl`, `benchmark/make_split.py`, `benchmark/evidence_check.csv` |
| Trace top-3 đầy đủ | `starter/results/traces/traces_all.jsonl` |
| Chỉ số dev/test | `starter/results/metrics_all.md` |
| Thống kê ingest, lỗi, nạp lại | `starter/results/ingest_runs.json`, `ingest_errors.csv`, `reingest_check.json` |
| Nhãn thủ công | `starter/results/audit/*_labels.csv`, `audit_summary.md` |
| Báo cáo | `report/individual/` |
