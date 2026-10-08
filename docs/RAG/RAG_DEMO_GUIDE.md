# Hướng dẫn chạy demo RAG từ A đến Z

Chạy tất cả lệnh tại `D:\Denso\project` bằng PowerShell. Dự án hiện ở gốc, không còn yêu cầu vào `denso_demo`. Demo dành cho một người trên máy Windows hiện tại. Các thư mục output phải mới; không ghi đè run/index cũ.

## A. Chuẩn bị môi trường

Nếu `.venv` đã có trên máy này, dùng trực tiếp. Khi tạo mới:

```powershell
Set-Location D:\Denso\project
py -3.12 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt -c cuda-constraints.txt
& ./.venv/Scripts/python.exe -m pip check
$env:PYTHONUTF8 = '1'
```

Python 3.12, driver NVIDIA tương thích CUDA, model tải vài GB và đủ dung lượng cho ảnh/index. `requirements.lock.txt` ghi môi trường thực tế. Không dùng Python mặc định khác phiên bản để chạy vào cùng venv.

## B. Tải model một lần

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 doctor --language en --download-models
& ./.venv/Scripts/python.exe -m denso_a3 doctor --language vi --download-models --output results/doctor_vi.json
& ./.venv/Scripts/python.exe -m denso_a3 rag-bootstrap
```

Đây là tải file model public, không gọi dịch vụ suy luận trả phí. Sau đó inference dùng file local. Manifest ở `.cache/rag/models.json` gồm BGE-M3, reranker, Qwen3-VL-2B và đường dẫn llama-server. Có thể tải E5 để thí nghiệm bằng `rag-bootstrap --include-e5`; phải build index riêng với `embedding_role=embedding_e5`.

Không bật API để xử lý lỗi local. Kiểm tra model/runtime thiếu, CUDA, port và log lỗi trước.

## C. Chọn hoặc tạo run BT1–BT3

Run có sẵn: `results/full_200` với 67 trang/ảnh logic. RAG reader dùng ảnh sao chép trong `assets/`, nên đường dẫn render cũ sau khi di chuyển project không gây mất ảnh.

Nếu cần chạy lại corpus:

```powershell
$run = "results/full_$(Get-Date -Format yyyyMMdd_HHmmss)"
& ./.venv/Scripts/python.exe -m denso_a3 run --output $run --language en
```

Với thư mục tài liệu riêng:

```powershell
$run = "results/upload_$(Get-Date -Format yyyyMMdd_HHmmss)"
& ./.venv/Scripts/python.exe -m denso_a3 run --input .local/my_documents --language vi --output $run
```

Thư mục input phải tồn tại và chứa PDF/ảnh. Chọn `vi` cho tài liệu Việt hoặc Việt/Anh. Chất lượng OCR dấu cần kiểm tra trên ảnh; `ok` là trạng thái xử lý, không phải kiểm duyệt nội dung.

## D. Build index đầu tiên

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-build --run results/full_200 --output results/rag_main --config configs/rag.local.json
```

Nếu `results/rag_main` đã tồn tại, dùng tên mới hoặc mở index có sẵn. Lệnh tạo caption ảnh, chunk, embedding, SQLite và Qdrant; có thể mất nhiều phút. Chỉ snapshot có `manifest.state=ready` được hỏi đáp.

`configs/rag.text.json` bỏ caption để build baseline nhanh hơn. `configs/rag.flat.json` dùng chunk văn bản phẳng để so với table-aware. Các index khác nhau phải ghi thư mục khác nhau.

## E. Mở UI

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-ui
```

Mở `http://127.0.0.1:8501`. Server chỉ bind máy local. Sidebar chọn index và file cấu hình. Mặc định `results/rag_main`; trạng thái index đã chọn được lưu ở `.local/workspace.json`.

Nếu chưa có index, tab **Tài liệu & upload** có nút tạo từ run BT1–BT3. Sau khi tạo snapshot khác, tải lại trang để sidebar mở index vừa lưu. Muốn dùng port UI khác: `rag-ui --port 8502`. Port generator mặc định 8091, độc lập với port UI.

## F. Hỏi một nguồn hoặc toàn kho

1. Tab **Hỏi đáp**, chọn **Chọn tài liệu** và một hay nhiều file; hoặc **Toàn bộ kho**.
2. Chọn chế độ tức thì/dài hạn.
3. Nhập câu hỏi tiếng Anh hoặc Việt, bấm **Trả lời**.
4. Mở từng citation để xem ảnh trang; kiểm tra số và đơn vị.
5. Mở **Chi tiết truy xuất / benchmark** khi cần xem plan, rankings, flags, timings.

Ví dụ chọn `images/24580.png`: “What is the name of the university?” hoặc “Tên trường đại học trong tài liệu là gì?”. Với câu biểu đồ, chọn đúng ảnh và ép `visual` khi Auto chưa nhận diện.

Ở **Nâng cao**, các lựa chọn là `auto`, `lexical`, `dense`, `hybrid`, `visual`. Đây là chiến lược retrieval/vision. Rerank, embedding device và caption là cấu hình JSON độc lập.

CLI tương đương:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index results/rag_main --source images/24580.png --question "What is the name of the university?" --config configs/rag.local.json
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index results/rag_main --source images/49153.png --strategy visual --question "What is the actual value per 1000 in 1975?" --config configs/rag.local.json
```

Bỏ `--source` để hỏi toàn kho. Dùng nhiều `--source` để chọn nhiều tài liệu. `--output results/answer.json` chủ động lưu kết quả; nếu không, chế độ tức thì chỉ in ra terminal, không lưu lịch sử.

## G. Hội thoại dài hạn

Chọn **Dài hạn**, nhập tên, bấm **Tạo hội thoại**, chọn cuộc trò chuyện. Có thể hỏi nối tiếp; đóng UI và mở lại vẫn đọc được SQLite `.local/conversations.sqlite`. Mỗi cuộc trò chuyện độc lập. Context rewrite chỉ dùng cùng index và hard filters; đổi nguồn/index sẽ không mang context cũ vào truy xuất mới.

Mode **Tức thì** không cần tạo conversation, mỗi câu độc lập và không ghi history. Đáp án gần nhất trong UI nằm trong session memory. Tài liệu upload và index vẫn lưu trên đĩa vì là kho tài liệu.

CLI nhận ID đã tạo qua UI bằng `--conversation ID`; có thể đổi vị trí SQLite bằng `--memory PATH`. Không tự dùng cùng ID cho các cuộc trò chuyện khác.

## H. Upload tài liệu mới

1. Tab **Tài liệu & upload** → chọn PDF/PNG/JPEG/TIFF/BMP; mỗi file tối đa 50 MB.
2. Chọn OCR `en` hoặc `vi`.
3. Bấm **Xử lý upload và tạo index mới**.
4. Chờ OCR/layout/bảng, caption và embedding hoàn tất.
5. Tải lại trang, kiểm tra file mới trong inventory rồi chọn nguồn để hỏi.

File được lưu vào `.local/uploads/<batch>/`; tên có batch ID để tránh đụng ID giữa các lần upload. Extraction lưu `.local/runs/`; index mới lưu `.local/indexes/`. Index mới hợp nhất các run cũ và run vừa upload, giữ metadata đã nhập khi hash nguồn khớp. Không ghi đè index cũ.

Nếu lỗi, UI báo lỗi và không chuyển sang snapshot chưa hoàn tất. File upload có thể vẫn còn trên đĩa để chạy lại. Không chạy build/benchmark khác dùng GPU đồng thời khi đang upload trong UI.

## I. Sửa metadata bằng form và JSON

Tab **Metadata**:

- Chọn tài liệu; nhập field và trạng thái `unreviewed`, `user_verified` hoặc `source_verified`.
- Ngày dùng `YYYY-MM-DD`. Không có dữ liệu thì để trống/null.
- Chỉ đánh dấu verified sau khi có căn cứ; hệ thống không tự xác minh nội dung bạn nhập.
- **Lưu form thành snapshot mới** không re-embed nội dung, chỉ cập nhật registry và payload.
- **Xuất metadata JSON**, chỉnh file, sau đó **Nhập** và **Áp dụng JSON đã nhập**.

Một field JSON có dạng:

```json
{
  "value": "PUMP-01",
  "origin": "user_form",
  "review_status": "user_verified",
  "evidence": "Trang 1, mã thiết bị ở tiêu đề"
}
```

Giữ `schema_version`, document ID, `source_relative`, `source_sha256` của bản export. Không dùng JSON giả định với ID tự đặt. JSON update phải chứa toàn bộ inventory của index hiện tại.

CLI:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-metadata --index results/rag_main --registry .local/metadata.json --output results/rag_metadata_v2
```

Hard filters trong UI/JSON chỉ dùng field được hỗ trợ. Ví dụ `{"equipment_id":"PUMP-01","revision_id":"R2"}` yêu cầu metadata đã verified; không khớp thì trả thiếu bằng chứng, không tự bỏ filter. `page_number` là số nguyên, source là đường dẫn tương đối chính xác.

## J. Kiểm tra conflict

Fixture số được kiểm tra trong `tests/test_rag.py`: 5 MPa và 6 MPa cùng family/thiết bị → cảnh báo; 5 MPa và 5000 kPa → không conflict; khác thiết bị/thuộc tính → không conflict.

Để thử UI với tài liệu của bạn, upload hai file revision khác tên, nhập family và equipment/part giống nhau, rồi hỏi trong phạm vi cả hai nguồn. Gate hiện nhận tốt dòng `Pressure: 5 MPa`, chưa bao phủ mọi câu văn/quy trình hoặc bảng phức tạp. Không dùng mtime để chọn revision. Nếu thiếu hiệu lực đã xác minh, cảnh báo và không kết luận.

## K. Chạy test và benchmark

```powershell
& ./.venv/Scripts/python.exe -m pytest -q
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_auto --config configs/rag.local.json
```

Mỗi output phải mới. `records.json` ghi cả câu lỗi; `summary.json` ghi config/latency/proxy metrics; `evidence_draft.json` có nhãn tự động chưa soát. Không bỏ câu lỗi khỏi báo cáo tổng.

Ví dụ baseline/ablation:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_A --strategy lexical --config configs/rag.baseline.json --retrieval-only
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_B --strategy dense --config configs/rag.baseline.json --retrieval-only
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_C --strategy hybrid --config configs/rag.baseline.json --retrieval-only
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_vi --language vi --limit 25 --config configs/rag.local.json
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_global --scope global --config configs/rag.local.json
```

`--retrieval-only` không sinh đáp án. `--language vi` dịch câu hỏi bằng model local trước; file dịch ghi `auto_translated_unreviewed`. Global variant thêm tên file vào câu như gợi ý lexical, không làm source hard filter; nó chỉ là proxy, không đại diện câu hỏi toàn kho thật. Chọn `--limit` để smoke test, bỏ để chạy đủ 50 câu.

Nhãn nháp hiện tìm substring đáp án trong chunk; không tìm được thì `not_located`. Hit@5 chỉ tính tập có nhãn. Không diễn giải chỉ số này là accuracy DENSO. Citation pointer hợp lệ cũng chưa đảm bảo citation chứng minh từng claim.

## L. Chuẩn bị API khi đã quyết định provider/ngân sách

Sao chép `configs/rag.api.example.json` sang cấu hình riêng, điền endpoint/model, đặt `api_enabled=true`, giữ key trong biến môi trường tên `DENSO_API_KEY` hoặc tên do bạn chọn. Điền giá input/output để báo chi phí ước lượng từ usage. Adapter cần provider hỗ trợ chat/completions, JSON và image content.

Mẫu API mặc định tắt và cố ý không chạy được cho đến khi cấu hình đủ. Chưa có cuộc gọi trả phí trong kiểm thử này. Giá trong JSON dùng để tính báo cáo, chưa phải quota/bộ chặn tổng ngân sách. Cần thêm giới hạn chi phù hợp nhà cung cấp trước khi chạy batch trả phí. Embedding/reranker vẫn local trong adapter hiện tại.

## M. Xử lý lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| Offline model missing | Chạy đúng bootstrap/doctor với ngôn ngữ rồi chạy offline lại |
| Output exists | Dùng tên snapshot/run mới |
| Port 8091 in use | Dừng tiến trình demo do bạn mở hoặc đổi `local_port`; không chiếm server khác |
| Qdrant lock | Đóng tiến trình đang mở cùng index; một process/request tại một thời điểm |
| CUDA unavailable/OOM | Đóng workload GPU khác; chọn CPU tường minh cho encoder/reranker; không có fallback âm thầm |
| Unknown citation/invalid JSON | Xem trace/evidence, giảm context hoặc thử model phù hợp; code không trả citation giả |
| Không có kết quả sau filter | Kiểm tra source path, verified status, revision; không tự bỏ filter |
| OCR Việt mất dấu | Mở ảnh nguồn, dùng `visual`, xem lại chất lượng ảnh; đừng coi OCR ok là nội dung đúng |
| PowerShell in lỗi tiếng Việt | Đặt `$env:PYTHONUTF8='1'` trước khi chạy |

## N. Artifact kiểm thử đã tạo

- `results/rag_text`: baseline text/table-aware 67 trang, 459 chunk, 261 nội dung embedding duy nhất; build khoảng 30,49 giây, không caption.
- `results/rag_eval_lexical`: 50 câu retrieval, không lỗi, nhãn nằm được ở 33 câu; Hit@5 nháp 32/33, P95 khoảng 0,122 giây.
- `results/rag_eval_dense`: 50 câu retrieval, không lỗi; Hit@5 nháp 33/33, P95 khoảng 6,357 giây, có model reload.
- `results/rag_vi_ocr_smoke`: một ảnh synthetic Việt, OCR/layout/bảng chạy GPU; inference khoảng 3,23 giây, còn lỗi dấu. Qwen VLM đọc trực tiếp đúng `5 MPa` và `220 V` trên ảnh này.
- `results/rag_main`: index đầy đủ có caption ảnh, 459 chunk / 261 nội dung embedding / 67 trang. Build thành công sau khi bổ sung xử lý caption không hợp lệ; lần build thành công mất khoảng 120,05 giây và có tái sử dụng cache, không phải thời gian tải model/build từ máy trống.
- `results/rag_eval_auto_final`: 50 câu end-to-end, 0 lỗi, 48 answered và 2 clarification; nguồn đều đúng scope và mọi citation trả về đều có pointer hợp lệ. Hit@5 nháp 31/33; answer substring 32/50 (64%); P50 12,25 giây, P95 19,94 giây, tối đa 21,30 giây. Model reload theo request. Auto bật vision ở 48 câu, chưa chứng minh tiết kiệm hơn luôn dùng VLM.
- `results/rag_eval_global_lexical_retry`: 50 câu lexical toàn kho, không lỗi, Hit@5 nháp 22/33; P95 khoảng 0,138 giây. Lần thử trước cùng lúc mở index với benchmark khác bị Qdrant lock, được giữ ở `results/rag_eval_global_lexical`; không dùng lần lỗi đó làm số đo chất lượng.

Hai clarification trong lượt 50 câu là false-positive cho cụm “current year”. Planner đã được sửa để phân biệt năm trong bảng với phiên bản hiện hành; regression được chạy riêng, không sửa đè raw records cũ. Lượt `results/rag_eval_auto_e2e` cũ đã dừng giữa chừng khi phát hiện lỗi citation format; không dùng làm kết quả cuối. Bản sau dùng constrained JSON schema, kiểm tra citation và một lần sinh lại nếu cần.

`results/rag_eval_current_year_regression` chạy lại hai câu sau sửa: `79.23%` và `20.77%`, cả hai khớp đáp án, không lỗi. P50 khoảng 27,12 giây và P95 32,99 giây trên chỉ hai mẫu; không dùng mẫu nhỏ này để suy rộng latency toàn hệ thống. Vì vậy không coi P95 19,94 giây của lượt 50 câu trước đó là bảo đảm mọi câu trả lời thực đều dưới 20 giây.

Kiểm thử trình duyệt thực: upload `.local/vi_smoke/vietnamese.png` với OCR `vi` tạo `.local/indexes/12ed4abbe483` (68 trang, 461 chunk, không caption lỗi, build 37,10 giây có cache). Hỏi áp suất trả `5 MPa` trong khoảng 19 giây; hỏi nối tiếp “Còn điện áp của nó?” được rewrite thành PUMP-01 và trả `220 V` trong khoảng 17,9 giây. Sau reload, cả hai lượt và citation vẫn xuất hiện trong hội thoại dài hạn. Form metadata tạo snapshot `.local/indexes/698f1771a493`, ghi tài liệu upload là `synthetic_test`, tiếng Việt, equipment PUMP-01, trạng thái nháp `unreviewed`. Các fixture này là dữ liệu kiểm thử tự tạo, không thuộc corpus nghiệp vụ.

Test tự động gồm 20 test extraction và 15 test RAG: prefilter lexical/dense, hash invalidation, persistence/scope, instant không mở memory, citation lạ/sinh lại, conflict số/đơn vị/bảng, metadata snapshot và router “current year”. `pip check` không có dependency hỏng. Test fixture/stub được phân biệt với các lần chạy model/GPU thật ở trên.

Các số trên không là benchmark accuracy nghiệp vụ; nhãn chưa soát, tập nhỏ, và không phải steady-state warm inference. Các kết quả end-to-end mới được ghi bằng lệnh ở phần K và phải đọc cùng config/manifest của chính lần chạy đó.
