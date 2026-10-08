# Bản đồ module và file của dự án DENSO A3

Ngày kiểm kê: 30/09/2026. Báo cáo này mô tả mã nguồn, cấu hình, tài liệu và test đang có trong `D:\Denso\project`. Phạm vi không đi vào từng ảnh/PDF của `evaluation/data/`, từng artifact sinh ra trong `results/`, model/cache trong `.cache/`, dữ liệu phiên làm việc trong `.local/`, môi trường `.venv/`, hoặc file `__pycache__/`. Với `.env`, chỉ kiểm tra tên biến, không đọc hay ghi lại giá trị.

## 1. Dự án làm gì và các module nối với nhau ra sao

Dự án có hai luồng chính:

```text
Trích xuất: python -m denso_a3
  → pipeline/cli.py → pipeline/runner.py
  → bt1/ingestion.py → pipeline/worker.py
  → backends/docling_backend.py → backends/normalization.py
  → bt2 + bt3 → evaluation/evaluator.py → review/html.py
  → results/<run>/

Hỏi đáp RAG: python -m denso_a3 rag-...
  → pipeline/rag_cli.py
  → pipeline/index_builder.py → bt4 → bt5 → results/<index>/
  → pipeline/rag_runner.py → bt6 → bt7 → AnswerRecord
  → CLI hoặc ui/app.py
```

| Module | Trách nhiệm và sản phẩm chính |
|---|---|
| `denso_a3/contracts.py`, `io.py` | Kiểu dữ liệu và cấu hình chung; kiểm tra tọa độ, hash và ghi artifact nguyên tử. |
| `bt1` | Quét nguồn, nhận dạng PDF/ảnh, render và chuẩn hóa thành ảnh trang; tạo định danh và metadata trang. |
| `bt2` | Văn bản OCR và tín hiệu cần soát. BT2 được tạo cùng BT3 trong một lần gọi Docling ở `backends`, không có engine độc lập trong `bt2/`. |
| `bt3` | Layout, lưới bảng, các dạng xuất bảng và gợi ý bảng nối trang. |
| `bt4` | Chia run đã trích xuất thành các chunk có vị trí bằng chứng; quản lý metadata tài liệu và trạng thái xác minh. |
| `bt5` | Lưu chunk vào SQLite FTS5 và vector vào Qdrant local; áp dụng cùng điều kiện lọc nguồn/metadata. |
| `bt6` | Lập kế hoạch câu hỏi, truy xuất lexical/dense, gộp/rerank kết quả và phát hiện mâu thuẫn số có cấu trúc. |
| `bt7` | Tạo câu trả lời có bằng chứng/citation, hoặc lưu hội thoại dài hạn trong SQLite. |
| `backends` | Adapter tới Docling, embedding/reranker, llama.cpp/API, tải model và đọc artifact run. |
| `pipeline` | CLI và điều phối các lượt trích xuất, xây index, hỏi đáp; quản lý worker GPU và snapshot. |
| `evaluation` trong `denso_a3` | Tính metric OCR/bảng/layout và benchmark RAG sau khi prediction đã được ghi. |
| `review`, `ui` | Trang HTML soát OCR offline và giao diện Streamlit hỏi đáp/upload/metadata. |

`evaluation/data/` chứa dữ liệu nguồn và sidecar đánh giá; `ground_truth/` chứa nhãn pilot. `results/` chứa các run và index được tạo, được Git bỏ qua. `.cache/` giữ ảnh render, prediction cache và model; `.local/` giữ upload, index/hội thoại của UI. Các vùng dữ liệu này có vai trò khác nhau và không phải module Python.

## 2. File ở thư mục gốc và quy tắc dự án

| File | Tác dụng |
|---|---|
| [README.md](../README.md) | Hướng dẫn chính: cài môi trường, chạy BT1–BT3 và RAG, contract, định dạng đầu ra và cách soát OCR. |
| [AGENTS.md](../AGENTS.md) | Chỉ dẫn làm việc trong repository; trỏ tới quy tắc tài liệu ở `.cursor/rules/`. |
| [.cursor/rules/project-documentation.mdc](../.cursor/rules/project-documentation.mdc) | Quy tắc hỏi định dạng báo cáo mỗi tác vụ, tổ chức mã theo trách nhiệm và cập nhật README khi thay đổi lớn về code/luồng/API. |
| [.gitignore](../.gitignore) | Bỏ qua venv, cache, `results/`, `.local/`, `.env`, bytecode và cache pytest khi dùng Git. |
| `.env` | Cấu hình máy cá nhân, hiện có tên biến `TESSERACT_CMD`. Mã pipeline hiện tại không đọc biến này; OCR chính dùng RapidOCR/Docling. Giá trị được giữ kín và file đã được Git bỏ qua. |
| [requirements.txt](../requirements.txt) | Danh sách dependency trực tiếp cho pipeline, RAG, UI và test. README yêu cầu cài cặp PyTorch CUDA trước. |
| [requirements.lock.txt](../requirements.lock.txt) | Phiên bản chốt của cả dependency trực tiếp lẫn phụ thuộc bắc cầu trong môi trường đã kiểm chứng. |
| [cuda-constraints.txt](../cuda-constraints.txt) | Ràng buộc phiên bản `torch`/`torchvision` bản CUDA 12.8 để tránh resolver chọn cặp khác. |

## 3. File lõi của package `denso_a3`

### Gốc package và hợp đồng chung

| File | Tác dụng |
|---|---|
| [__init__.py](../denso_a3/__init__.py) | Đánh dấu package gốc; chỉ có mô tả, không chạy pipeline. |
| [__main__.py](../denso_a3/__main__.py) | Entry point cho `python -m denso_a3`; chuyển quyền cho `pipeline.cli.main`. |
| [contracts.py](../denso_a3/contracts.py) | Định nghĩa `PipelineConfig`, `PageInput`, `RagConfig`, `ChunkRecord`, `QueryPlan`, `RetrievedEvidence`, `AnswerRecord`; kiểm tra cấu hình và chuẩn hóa bbox vào `[0,1]` theo ảnh trang. |
| [io.py](../denso_a3/io.py) | Tính SHA-256/fingerprint và ghi JSON/text bằng file tạm rồi thay thế nguyên tử. |

### `backends/`: thư viện ngoài và artifact

| File | Tác dụng |
|---|---|
| [__init__.py](../denso_a3/backends/__init__.py) | Đánh dấu package adapter và mô tả kiến trúc một lần chuyển đổi Docling cho BT2/BT3. |
| [artifacts.py](../denso_a3/backends/artifacts.py) | Đọc run BT1–BT3 đã hoàn tất, kiểm tra schema/số trang/page ID, fingerprint và hash ảnh trước khi RAG dùng. |
| [bootstrap.py](../denso_a3/backends/bootstrap.py) | Tải model embedding, reranker, Qwen VLM và llama.cpp runtime khi chạy `rag-bootstrap`; ghi manifest model. |
| [docling_backend.py](../denso_a3/backends/docling_backend.py) | Cấu hình Docling + RapidOCR Torch + layout + TableFormer, xác nhận vị trí thiết bị, chuyển một ảnh trang thành payload BT2/BT3 và thông tin runtime. |
| [generation.py](../denso_a3/backends/generation.py) | Chuẩn bị ảnh cho VLM, tự khởi động/đóng `llama-server` local hoặc gọi API chat/completions được bật tường minh; trả JSON và usage. |
| [normalization.py](../denso_a3/backends/normalization.py) | Chuyển output native của Docling thành schema BT2 text/spans và BT3 blocks/tables; giữ tọa độ, ô chưa biết, lỗi geometry và trạng thái partial. |
| [rag_models.py](../denso_a3/backends/rag_models.py) | Nạp encoder đa ngôn ngữ và reranker local, tạo embedding/điểm rerank, xử lý văn bản dài bằng cửa sổ và giải phóng model. |

### BT1–BT3: nhập liệu, OCR, bảng

| File | Tác dụng |
|---|---|
| [bt1/__init__.py](../denso_a3/bt1/__init__.py) | Đánh dấu package BT1; không có logic chạy. |
| [bt1/ingestion.py](../denso_a3/bt1/ingestion.py) | Quét PDF/ảnh, bỏ qua sidecar, kiểm tra file lỗi, render PDF hoặc xoay ảnh EXIF thành PNG canonical; tạo `documents`, `PageInput[]`, hash và nhóm ảnh trùng. |
| [bt2/__init__.py](../denso_a3/bt2/__init__.py) | Đánh dấu package BT2; không có logic chạy. |
| [bt2/quality.py](../denso_a3/bt2/quality.py) | Tính tín hiệu thô như text rỗng, số ký tự/span, tỷ lệ span có confidence thấp; đánh dấu chưa được người soát, không coi confidence là độ chính xác. |
| [bt3/__init__.py](../denso_a3/bt3/__init__.py) | Đánh dấu package BT3; không có logic chạy. |
| [bt3/tables.py](../denso_a3/bt3/tables.py) | Xuất bảng sang HTML giữ ô gộp và Markdown để xem; tạo ứng viên bảng nối giữa hai trang kề nhau, không tự ghép bảng. |

### BT4–BT7: lập chỉ mục và hỏi đáp

| File | Tác dụng |
|---|---|
| [bt4/__init__.py](../denso_a3/bt4/__init__.py) | Đánh dấu package BT4. |
| [bt4/chunking.py](../denso_a3/bt4/chunking.py) | Chia prose theo ngân sách token, nhóm hàng bảng với heading/header, fallback OCR span và tạo visual record cho mỗi trang; giữ `page_id`, `block_id`, `span_id`, bbox và parent. |
| [bt4/metadata.py](../denso_a3/bt4/metadata.py) | Tạo/kiểm tra registry metadata theo document ID và hash nguồn; chỉ trường `user_verified`/`source_verified` được lấy làm giá trị xác minh. |
| [bt5/__init__.py](../denso_a3/bt5/__init__.py) | Đánh dấu package BT5. |
| [bt5/store.py](../denso_a3/bt5/store.py) | Lưu chunk trong SQLite và chỉ mục từ khóa FTS5, vector trong Qdrant local; lọc trước top-k, tìm lexical/dense và cập nhật metadata payload. |
| [bt6/__init__.py](../denso_a3/bt6/__init__.py) | Đánh dấu package BT6. |
| [bt6/planner.py](../denso_a3/bt6/planner.py) | Xác định ngôn ngữ, intent bảng/ảnh/phiên bản, hard filter và soft preference; yêu cầu làm rõ khi revision hoặc câu hỏi mơ hồ chưa có căn cứ. |
| [bt6/retrieval.py](../denso_a3/bt6/retrieval.py) | Thực hiện lexical/dense theo chiến lược, bỏ lặp nội dung alias, gộp hạng bằng RRF, boost metadata/bảng, rerank và trả evidence kèm trace thời gian. |
| [bt6/conflicts.py](../denso_a3/bt6/conflicts.py) | So các fact số có nhãn/đơn vị giữa tài liệu cùng family và cùng thiết bị/part đã xác minh; sinh `potential_conflict` khi giá trị khác nhau. |
| [bt7/__init__.py](../denso_a3/bt7/__init__.py) | Đánh dấu package BT7. |
| [bt7/answering.py](../denso_a3/bt7/answering.py) | Đưa evidence/ảnh vào model, buộc câu trả lời JSON, kiểm tra citation ID và ảnh nguồn, thử sinh lại một lần nếu output không hợp lệ. |
| [bt7/memory.py](../denso_a3/bt7/memory.py) | Lưu danh sách hội thoại và các lượt hỏi đáp trong SQLite; đọc lịch sử theo conversation và scope để hỗ trợ hỏi nối tiếp. |

### `pipeline/`: điều phối và CLI

| File | Tác dụng |
|---|---|
| [__init__.py](../denso_a3/pipeline/__init__.py) | Đánh dấu package điều phối. |
| [cli.py](../denso_a3/pipeline/cli.py) | Parser chung của `ingest`, `doctor`, `run`, `evaluate`; chuyển lệnh `rag-*` sang `rag_cli.py`, chọn output mặc định và báo lỗi. |
| [runner.py](../denso_a3/pipeline/runner.py) | Điều phối một run BT1–BT3: ingest → worker/cache → artifact từng trang và tổng hợp → metric → `review.html`; từ chối ghi đè run đã có. |
| [worker.py](../denso_a3/pipeline/worker.py) | Chạy backend GPU trong process `spawn` riêng, trao đổi qua Pipe, áp timeout và dừng/khởi động lại worker khi cần. |
| [rag_config.py](../denso_a3/pipeline/rag_config.py) | Đọc JSON thành `RagConfig`, kiểm tra tham số, chuẩn hóa đường dẫn model manifest và đọc manifest. |
| [index_builder.py](../denso_a3/pipeline/index_builder.py) | Hợp nhất run trích xuất, tạo registry/chunk/caption/embedding, ghi SQLite + Qdrant + assets vào staging; chỉ đổi tên thành index `ready` sau khi hoàn tất. Cập nhật metadata bằng snapshot mới. |
| [rag_runner.py](../denso_a3/pipeline/rag_runner.py) | Điều phối một câu hỏi: kiểm tra index, viết lại câu theo history nếu dài hạn, plan → retrieve → kiểm tra phiên bản/mâu thuẫn → trả lời/citation → ghi memory. |
| [rag_cli.py](../denso_a3/pipeline/rag_cli.py) | Parser cho `rag-bootstrap`, `rag-build`, `rag-ask`, `rag-metadata`, `rag-evaluate`, `rag-ui`; gọi các hàm điều phối tương ứng. |

### `evaluation/`, `review/`, `ui/` trong package

| File | Tác dụng |
|---|---|
| [evaluation/__init__.py](../denso_a3/evaluation/__init__.py) | Đánh dấu package đánh giá prediction đã lưu. |
| [evaluation/text.py](../denso_a3/evaluation/text.py) | Chuẩn hóa text, tính CER/WER bằng Levenshtein và tổng hợp micro/macro. |
| [evaluation/structural.py](../denso_a3/evaluation/structural.py) | So bbox one-to-one theo IoU, đánh giá detection/cell/grid bảng và recall layout trên phần pilot có nhãn. |
| [evaluation/evaluator.py](../denso_a3/evaluation/evaluator.py) | Sau khi đã ghi prediction, đọc `.ocr` và `ground_truth/pilot.json`, căn trang, tính metric BT2/BT3, ghi `metrics.json` và registry tham chiếu. |
| [evaluation/rag_evaluator.py](../denso_a3/evaluation/rag_evaluator.py) | Đọc bộ QA để tạo nhãn evidence nháp; chạy retrieval-only hoặc hỏi đáp đầy đủ theo strategy/scope/ngôn ngữ, ghi `records.json` tăng dần và `summary.json` cuối lượt. |
| [review/__init__.py](../denso_a3/review/__init__.py) | Đánh dấu package review offline. |
| [review/html.py](../denso_a3/review/html.py) | Sao chép ảnh vào run và nhúng dữ liệu trang/OCR/bảng/metric vào template để tạo `review.html` tự chứa. |
| [review/template.html](../denso_a3/review/template.html) | Giao diện review trong trình duyệt: ảnh, overlay bbox, text OCR/reference/diff, bảng, metric và xuất ghi chú JSON. |
| [ui/__init__.py](../denso_a3/ui/__init__.py) | Đánh dấu package giao diện local. |
| [ui/app.py](../denso_a3/ui/app.py) | Streamlit UI một người dùng: chọn index, hỏi đáp, xem citation, hội thoại, upload PDF/ảnh, tạo index mới và sửa/import/export metadata. |

## 4. File cấu hình RAG

Các JSON trong `configs/` là các lớp ghi đè giá trị mặc định của `RagConfig`, được đọc qua `pipeline/rag_config.py`.

| File | Tác dụng |
|---|---|
| [rag.local.json](../configs/rag.local.json) | Cấu hình demo local: CUDA cho build/query/rerank, bật caption ảnh và rerank, sinh câu trả lời bằng model local. |
| [rag.text.json](../configs/rag.text.json) | Cấu hình local thiên về text: không tạo caption ảnh, vẫn bật rerank. |
| [rag.baseline.json](../configs/rag.baseline.json) | Baseline so sánh truy xuất: tắt caption, rerank và metadata boost. |
| [rag.flat.json](../configs/rag.flat.json) | Ablation chunk `flat`, đồng thời tắt caption/rerank/metadata boost để so với chunk có cấu trúc. |
| [rag.api.example.json](../configs/rag.api.example.json) | Mẫu cấu hình API bên ngoài; `api_enabled=false` nên chưa kích hoạt gọi API, key được lấy qua tên biến môi trường khi bật. |

## 5. Dữ liệu nhãn và các file test

| File | Tác dụng |
|---|---|
| [ground_truth/create_pilot.py](../ground_truth/create_pilot.py) | Tái tạo `pilot.json` từ nhãn xem ảnh nguồn của 5 trang PDF; kiểm tra SHA nguồn trước khi ghi để tránh dùng nhãn cho file đã đổi. |
| [ground_truth/pilot.json](../ground_truth/pilot.json) | Nhãn pilot về bảng và một phần layout; chỉ một bảng có nhãn cell/topology đầy đủ. `review_status=needs_user_review`, chưa phải ground truth đã xác nhận. |
| [tests/test_pipeline.py](../tests/test_pipeline.py) | Test BT1–BT3: ingest, PDF/EXIF/geometry, table/grid, CER/WER, cache, review và timeout worker. |
| [tests/test_rag.py](../tests/test_rag.py) | Test RAG: lọc lexical/dense, mâu thuẫn, revision, memory, citation, metadata snapshot và routing `current year`. |
| [tests/legacy_bt2_baseline.py](../tests/legacy_bt2_baseline.py) | Bộ test lịch sử cho baseline Tesseract cũ. Nó import các module `denso_a3.bt2.baseline`, `cli`, `input_adapter`, `pdf_reader`, `tesseract` và contract `TextRead` không còn trong cây mã hiện tại; tên file cũng không theo mẫu `test_*.py` mặc định. Không dùng nó làm bằng chứng test hiện tại chạy được. |
| [evaluation/__init__.py](../evaluation/__init__.py) | Package `evaluation` cấp repository từ framework đánh giá trước đây; hiện chỉ là mô tả. Khác với `denso_a3/evaluation/` đang được pipeline dùng. |
| [evaluation/metrics/__init__.py](../evaluation/metrics/__init__.py) | File export tên các hàm metric của framework cũ; các module nó import (`answer_correctness`, `retrieval_metrics`, `latency`) không có trong thư mục hiện tại. Không thuộc luồng đánh giá RAG đang chạy. |

Trong phạm vi dữ liệu được loại khỏi kiểm kê từng file, `evaluation/data/dataset_test.json` là bộ QA để benchmark RAG; `nhwc0428.ocr` là text tham chiếu của PDF để đo mức khớp OCR; ảnh/PDF là input trích xuất. Bộ QA không được dùng làm đáp án để sinh prediction BT1–BT3.

## 6. Tài liệu trong `docs/`

| File | Tác dụng |
|---|---|
| [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) | Báo cáo triển khai và kết quả chạy thật BT1–BT3: kiến trúc Docling, contract, pilot DPI, cache, metric và giới hạn kết luận. |
| [RAG_PIPELINE_PLAN.md](RAG_PIPELINE_PLAN.md) | Kế hoạch/giả thuyết RAG, lựa chọn kiến trúc, luồng BT4–BT7, tiêu chí benchmark và rủi ro; có nội dung dự kiến, không phải mọi mục đều đã triển khai. |
| [RAG_ARCHITECTURE_IMPLEMENTATION.md](RAG_ARCHITECTURE_IMPLEMENTATION.md) | Mô tả kiến trúc RAG đã triển khai, vai trò module, bước build index và luồng một câu hỏi. |
| [RAG_DEMO_GUIDE.md](RAG_DEMO_GUIDE.md) | Hướng dẫn vận hành A–Z: bootstrap, build, UI, hỏi đáp, upload, metadata, benchmark và xử lý lỗi thường gặp. |
| [RAG_TECHNICAL_EXPLAINED.md](RAG_TECHNICAL_EXPLAINED.md) | Giải thích nguyên lý chunk, embedding, hybrid retrieval, metadata, VLM, citation, memory và đánh giá. |
| [PROJECT_MODULE_FILE_GUIDE.md](PROJECT_MODULE_FILE_GUIDE.md) | Báo cáo hiện tại: bản đồ module, trách nhiệm từng file và các điểm cần lưu ý khi đọc repository. |

## 7. Điểm cần nhớ khi đọc repository

1. `__init__.py` của `denso_a3` và các package con chỉ là mô tả/đánh dấu package; logic thực tế nằm trong các file chức năng ở bảng trên.
2. Một lần chuyển đổi Docling sinh đồng thời BT2 và BT3. Không có pipeline OCR Tesseract đang chạy trong `denso_a3/bt2/`.
3. `results/<run>/` là output trích xuất; `results/<index>/` là snapshot RAG; `results/rag_eval_*/` là benchmark. `rag_main` dùng `full_200` làm input, còn UI có thể tạo run/index mới trong `.local/`.
4. `README.md` hiện còn nhắc `docs/BT1_BT2_BT3_Ingestion_Extraction_Plan.md`, `docs/BT2_BASELINE_README.md` và `requirements.bt2-baseline.txt`, nhưng ba file này không có trong cây dự án hiện tại. Các phần hướng dẫn chính của RAG và BT1–BT3 nằm trong những file `docs/` đã liệt kê ở trên.
5. Báo cáo này là kiểm kê tĩnh theo mã và tài liệu đang có. Không chạy model/GPU hoặc benchmark; thông số chất lượng/thời gian lịch sử cần đọc từ `results/` và các báo cáo kiểm thử liên quan.
