# Luồng code khi upload một tài liệu mới qua giao diện

Ngày đọc code: 30/09/2026. Tình huống giả định: người dùng mở Streamlit bằng `python -m denso_a3 rag-ui`, chọn một file PDF hoặc ảnh ở tab **Tài liệu & upload**, chọn ngôn ngữ OCR rồi bấm **Xử lý upload và tạo index mới**. Không có upload thật được thực hiện để viết báo cáo này.

Ký hiệu trong các đường dẫn dưới đây:

- `{T}` là 12 ký tự hex ngẫu nhiên, sinh riêng cho lần bấm nút upload.
- `{S}` là 8 ký tự hex dùng cho thư mục index tạm.
- `{H}` là SHA-256 hoặc fingerprint của nội dung/cấu hình tương ứng.
- Ví dụ tên file người dùng chọn là `manual.pdf`; đường dẫn thực tế mang tên file họ upload.

## 1. Tóm tắt thứ tự gọi hàm

```text
python -m denso_a3 rag-ui
  denso_a3/__main__.py
    → pipeline/cli.py:main
    → pipeline/rag_cli.py:main
    → Streamlit chạy ui/app.py:main

Người dùng bấm "Xử lý upload và tạo index mới"
  ui/app.py:main
    → ghi file gốc vào .local/uploads/{T}/
    → ui/app.py:run_command("run", ...)
      → pipeline/cli.py:main
      → pipeline/runner.py:run
        → bt1/ingestion.py:ingest
        → pipeline/worker.py:InferenceWorker
          → backends/docling_backend.py:DoclingBackend.convert
          → backends/normalization.py:normalize_payload
          → bt2/quality.py + bt3/tables.py
        → evaluation/evaluator.py:evaluate
          → evaluation/text.py + evaluation/structural.py
        → review/html.py:create_review
    → ui/app.py:run_command("rag-build", ...)
      → pipeline/cli.py → pipeline/rag_cli.py
      → pipeline/rag_config.py:load_config
      → pipeline/index_builder.py:build_index
        → backends/artifacts.py:load_run/page_image
        → bt4/metadata.py + bt4/chunking.py
        → backends/generation.py:Generator (caption ảnh nếu bật)
        → backends/rag_models.py:TextEncoder
        → bt5/store.py:RetrievalStore
    → ui/app.py:activate
      → .local/workspace.json
```

Các lệnh `run` và `rag-build` được UI gọi bằng **subprocess Python riêng** qua `run_command`, không chạy trực tiếp trong process Streamlit. Điều này tách vòng đời model/GPU của OCR và bước build index khỏi UI ([ui/app.py](../denso_a3/ui/app.py), [rag_cli.py](../denso_a3/pipeline/rag_cli.py)).

## 2. Từng giai đoạn và file được tạo

### A. Nhận file từ giao diện

Ở [ui/app.py, dòng 76–106](../denso_a3/ui/app.py), uploader nhận PDF, PNG, JPEG, TIFF hoặc BMP, giới hạn 50 MB mỗi file. Ngôn ngữ OCR do người dùng chọn `en` hoặc `vi`; mặc định của widget là `en`. Khi bấm nút, code tạo `{T}`, lấy riêng **tên file cuối** từ tên upload, thêm `{T}_000_` rồi ghi bytes gốc:

```text
D:\Denso\project\.local\uploads\{T}\{T}_000_manual.pdf
```

Tiền tố này tạo định danh nguồn riêng cho lần upload. Nếu upload một ảnh thì cấu trúc tương tự, ví dụ `{T}_000_photo.png`. File này là bản gốc của upload và vẫn nằm trong `.local/uploads/` sau khi build.

### B. Chạy BT1–BT3 cho tài liệu mới

UI gọi lệnh tương đương:

```powershell
python -m denso_a3 run --input "D:\Denso\project\.local\uploads\{T}" --language en --output "D:\Denso\project\.local\runs\{T}"
```

`--language` sẽ là `vi` nếu chọn tiếng Việt. Các tùy chọn khác giữ mặc định từ [pipeline/cli.py](../denso_a3/pipeline/cli.py): DPI 200, thiết bị CUDA, RapidOCR Torch, TableFormer accurate, model local đã cài. Lệnh không tự bật tải model; nếu model thiếu hoặc GPU không sẵn sàng, nó báo lỗi.

1. [bt1/ingestion.py](../denso_a3/bt1/ingestion.py) quét folder upload. Với PDF, nó đọc từng trang, ghi nhận metadata/text layer nhưng vẫn render ảnh và OCR mới. Với ảnh, nó áp EXIF orientation và chuyển sang PNG RGB. Nó tạo document ID, page ID, hash nguồn/ảnh, kích thước, số trang và metadata geometry. Ảnh canonical được giữ ở `.cache/renders/{H}.png` để dùng lại; PDF nhiều trang tạo nhiều ảnh. File đầu vào mang tên đã thêm tiền tố sẽ xuất hiện trong `source_relative` của run/index.
2. [pipeline/runner.py](../denso_a3/pipeline/runner.py) khởi động [InferenceWorker](../denso_a3/pipeline/worker.py) ở process riêng. Worker nạp [DoclingBackend](../denso_a3/backends/docling_backend.py): RapidOCR, layout và TableFormer. Mỗi ảnh trang qua một lần `convert`; [normalization.py](../denso_a3/backends/normalization.py) tách kết quả thành BT2 text/spans và BT3 blocks/tables. Nếu có prediction cache hợp lệ ở `.cache/predictions/{H}.json`, runner dùng lại; worker vẫn được khởi động để xác minh runtime/model trước khi dùng cache. Cache prediction mới chỉ được ghi khi chuyển đổi thành công và không có lỗi BT2/BT3.
3. Runner ghi artifact từng trang và tổng hợp vào `.local/runs/{T}/`, xuất bảng bằng [bt3/tables.py](../denso_a3/bt3/tables.py). Sau đó [evaluation/evaluator.py](../denso_a3/evaluation/evaluator.py) tính metric nếu có tham chiếu phù hợp và [review/html.py](../denso_a3/review/html.py) tạo trang soát offline, sao chép ảnh trang vào `assets/` của run.

Các file chính của **run mới**:

```text
D:\Denso\project\.local\runs\{T}\
  documents.json                 # Danh sách tài liệu, nguồn, hash, trạng thái
  pages.json                     # Danh sách trang logic, ảnh, geometry, page ID
  ingestion.json                 # Tổng kết BT1, ảnh trùng, sidecar bỏ qua
  run_config.json                # Input, ngôn ngữ, DPI, device, số trang
  runtime.json                   # Phiên bản/model/device thực tế
  bt2.json                       # OCR của tất cả trang
  bt3.json                       # Layout và bảng của tất cả trang
  execution.json                 # Mỗi trang inferred/cache/failed, timing
  continuation_candidates.json   # Gợi ý bảng nối trang, nếu có
  ground_truth_registry.json     # Registry tham chiếu dùng cho đánh giá
  metrics.json                   # Metric hoặc N/A nếu chưa có nhãn
  performance.json               # Thời gian, cache, trạng thái BT2/BT3
  review.html                    # Trang review offline
  assets/{H}.png                 # Bản sao ảnh canonical để review
  raw/{H}.json                   # Raw Docling/OCR cells, nếu có payload
  pages/{page_id}/
    ocr.txt
    bt2.json
    bt3.json
    table_0.html                 # Chỉ có nếu trang phát hiện bảng
    table_0.md                   # Chỉ có nếu trang phát hiện bảng
```

Tên thư mục `pages/{page_id}` thực tế thay dấu `:` của page ID bằng `_`. `startup_error.txt` chỉ xuất hiện nếu worker không khởi động được. Với một file upload mới không kèm `.ocr` và không có nhãn pilot trùng nguồn, `metrics.json` vẫn được tạo nhưng CER/WER và metric bảng có thể là N/A; đó không phải xác nhận OCR đúng.

### C. Xây index RAG mới

Sau khi lệnh `run` thành công, UI gọi `rag-build`. Có hai nhánh trong [ui/app.py, dòng 100–106](../denso_a3/ui/app.py):

| Trạng thái trước upload | Danh sách `--run` dùng để build | Metadata |
|---|---|---|
| Đã có index `ready` đang chọn | Các `run_paths` trong manifest của index cũ **cộng** `.local/runs/{T}` | Truyền registry của index cũ để giữ metadata đã nhập/xác minh. |
| Chưa có index `ready` | Chỉ `.local/runs/{T}` | Tạo registry mặc định cho tài liệu mới. |

Như vậy khi đã có `rag_main` đang mở, hệ thống **xây một snapshot chứa kho cũ và tài liệu mới**. Nó không thêm chunk vào index cũ tại chỗ. Cấu hình mặc định trên UI là `configs/rag.local.json`; người dùng có thể đổi đường dẫn cấu hình trong sidebar.

[pipeline/index_builder.py](../denso_a3/pipeline/index_builder.py) lần lượt:

1. Tạo `.local/indexes/{T}.building-{S}/` với `manifest.json` mang `state=building`.
2. Dùng [backends/artifacts.py](../denso_a3/backends/artifacts.py) đọc và kiểm tra mọi run đầu vào đã hoàn tất; hợp nhất tài liệu/trang theo ID, từ chối trùng ID có hash nguồn khác.
3. Dùng [bt4/metadata.py](../denso_a3/bt4/metadata.py) tạo registry và, nếu có index cũ, kiểm tra source hash trước khi giữ metadata cũ. Tài liệu mới bắt đầu với metadata nghiệp vụ chưa xác minh.
4. Dùng [bt4/chunking.py](../denso_a3/bt4/chunking.py) chia text, bảng và visual record thành chunk có nguồn/page/bbox. Sao chép ảnh canonical vào `assets/` và file gốc còn tồn tại, đúng hash vào `sources/` của snapshot.
5. Với `rag.local.json`, `caption_images=true`: dùng [backends/generation.py](../denso_a3/backends/generation.py) tạo mô tả ảnh cho visual chunk. Caption tái dùng hoặc ghi vào `.cache/rag/captions/{H}.json`; mô tả này được đánh dấu chưa kiểm duyệt.
6. Dùng [backends/rag_models.py](../denso_a3/backends/rag_models.py) tạo embedding; [bt5/store.py](../denso_a3/bt5/store.py) ghi text/metadata vào SQLite FTS5 và vector vào Qdrant local.
7. Ghi manifest `state=ready` cùng JSON của index, rồi đổi tên folder staging thành `.local/indexes/{T}/`. Chỉ index `ready` được UI nhận để hỏi đáp.

Các file chính của **index mới**:

```text
D:\Denso\project\.local\indexes\{T}\
  manifest.json         # ready, run_paths, config, model, số chunk/trang
  registry.json         # Metadata và provenance theo tài liệu
  documents.json        # Danh sách tài liệu đã hợp nhất
  pages.json            # Danh sách trang đã hợp nhất
  chunks.json           # Chunk text/bảng/visual và pointer nguồn
  parents.json          # Context cha, đặc biệt bảng/trang
  records.sqlite        # Chunk records và chỉ mục lexical FTS5
  vectors/              # Qdrant local vector index
  assets/{H}.png        # Ảnh canonical để dẫn nguồn/VLM
  sources/{H}.pdf       # Bản sao file nguồn nếu hash hợp lệ; ảnh có đuôi ảnh
```

Tên `{H}.pdf` trong `sources/` chỉ là ví dụ cho upload PDF. Nếu build lỗi, folder `.building-{S}` có thể còn lại và không được xem là index hoàn chỉnh. Index cũ vẫn ở vị trí cũ.

### D. Kích hoạt index mới

Khi cả `run` và `rag-build` trả về thành công, [ui/app.py:activate](../denso_a3/ui/app.py) ghi:

```text
D:\Denso\project\.local\workspace.json
  {"index": "D:\\Denso\\project\\.local\\indexes\\{T}"}
```

UI hiện thông báo cần tải lại trang. Khi mở lại, sidebar lấy index từ `workspace.json`; tài liệu mới xuất hiện trong danh sách và có thể được truy xuất. Bản upload gốc, run mới, index mới và cache vẫn tồn tại. Upload không tự ghi vào `evaluation/data/` hay `results/`, không tự chạy `rag-evaluate`, và không tạo lượt hội thoại; `.local/conversations.sqlite` chỉ được dùng khi người dùng chọn hội thoại dài hạn rồi hỏi.

## 3. Điểm dễ nhầm

- **Một file PDF có thể sinh nhiều trang OCR và nhiều chunk.** Số file upload bằng 1 không có nghĩa chỉ có 1 `PageInput` hay 1 vector.
- **Tên file nguồn thay đổi có chủ đích.** `manual.pdf` thành `{T}_000_manual.pdf` trong kho upload; định danh tài liệu tính từ đường dẫn tương đối đó.
- **Index mới có thể chứa lại toàn bộ kho cũ.** Nếu index đang chọn đã `ready`, builder đọc các `run_paths` cũ cộng run mới, sao chép lại ảnh/nguồn và xây vector snapshot mới. Điều này làm tăng thời gian và dung lượng theo quy mô kho.
- **Metric OCR có thể không có số đo.** Nếu tài liệu mới không có transcript/nhãn tham chiếu, `metrics.json` ghi trạng thái chưa đánh giá; `ok` của BT2/BT3 chỉ nói pipeline/schema đã chạy.
- **Lỗi không tự kích hoạt dữ liệu dở.** `run_command` ném lỗi nếu subprocess trả mã khác 0; UI hiển thị lỗi, không gọi `activate` khi một bước thất bại. Artifact run hoặc staging có thể còn để điều tra.

File điều hướng chung của dự án: [PROJECT_MODULE_FILE_GUIDE.md](PROJECT_MODULE_FILE_GUIDE.md).
