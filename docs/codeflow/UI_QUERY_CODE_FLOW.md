# Luồng code khi hỏi “Pump-01 là gì?” trên Streamlit

Ngày kiểm tra: **01/10/2026**. Thư mục dự án: `D:\Denso\project`.

Báo cáo lần theo code và dữ liệu index hiện có, từ lúc người dùng nhập câu hỏi rồi bấm **Trả lời**, đến lúc giao diện hiển thị kết quả. Trường hợp chính dùng lựa chọn mặc định: **Toàn bộ kho**, **Tức thì — mỗi câu độc lập**, kiến trúc **auto**, cấu hình `configs/rag.local.json`, hard filters `{}`. Phần 7 giải thích thay đổi khi chọn hội thoại dài hạn hoặc phạm vi khác.

Đã chạy trực tiếp hàm planner với đúng câu hỏi mẫu và đọc SQLite bằng kết nối chỉ đọc. **Chưa chạy retrieval bằng model, chưa gọi model sinh đáp án, chưa gửi câu hỏi thật trên trình duyệt**. Vì vậy thứ tự retrieval và nội dung đáp án cuối được trình bày theo điều kiện trong code; không có kết quả inference hoặc số đo thời gian giả định.

## 1. Câu hỏi này đi qua đâu?

Sau khi Streamlit đã mở, nút **Trả lời** gọi trực tiếp hàm Python `ask()` trong process Streamlit. `ask()` đọc index đã có, tìm các đoạn bằng chứng, kiểm tra mâu thuẫn, rồi gọi model local để viết câu trả lời. Cuối cùng UI giữ kết quả trong session và hiển thị đáp án, ảnh dẫn nguồn, thông tin truy xuất.

Với chế độ tức thì, code không xuất một file JSON hoặc TXT chứa câu hỏi/đáp án, không chạy lại OCR và không tạo index mới. Vector của câu hỏi và danh sách bằng chứng được xử lý trong bộ nhớ. Qdrant vẫn mở các file lưu trữ và khóa nội bộ; chi tiết ở phần 5.

```text
Khởi động giao diện, nếu chưa chạy:
  python -m denso_a3 rag-ui
  __main__.py → pipeline/cli.py:main → pipeline/rag_cli.py:main
    → subprocess chạy Streamlit với ui/app.py
    → http://127.0.0.1:8501

Nhập “Pump-01 là gì ?” và bấm Trả lời:
  ui/app.py:main
    → pipeline/rag_config.py:load_config
    → pipeline/rag_runner.py:ask
        → đọc manifest.json, registry.json
        → [nếu dài hạn] bt7/memory.py:ConversationStore.history
          → [nếu có lịch sử phù hợp] Generator.generate_json để viết lại câu hỏi
        → bt6/planner.py:plan_query
        → bt5/store.py:RetrievalStore
        → bt6/retrieval.py:retrieve
            → store.allowed: xác định chunk đủ điều kiện
            → store.lexical: SQLite FTS5/BM25
            → backends/rag_models.py:TextEncoder.encode
            → store.dense: Qdrant local
            → gộp thứ hạng, cộng ưu tiên metadata
            → [nếu cần] Reranker.score
            → chọn bằng chứng E1…E5
        → [nếu auto và top 2 có ảnh/figure] bật nhánh visual
        → bt6/conflicts.py:detect_conflicts
        → bt7/answering.py:grounded_answer
            → đọc parents.json
            → [nếu visual] đọc assets/<image_sha256>.png
            → backends/generation.py:Generator.generate_json
                → Generator.start: chạy llama-server.exe bằng subprocess
                → POST http://127.0.0.1:8091/v1/chat/completions
            → citations_for: kiểm tra citation và ảnh nguồn
        → [nếu dài hạn] ConversationStore.append
        → Generator.close, đóng memory, trả dict AnswerRecord
    → st.session_state["last_answer"]
    → ui/app.py:show_answer
```

Đây là cây gọi hàm, không phải mọi nhánh đều chạy trong mọi lượt hỏi. Nếu cần làm rõ, không có bằng chứng, có mâu thuẫn hoặc phát sinh lỗi, luồng có thể kết thúc trước bước sinh đáp án.

## 2. Dữ liệu mà giao diện hiện có thể dùng

File [workspace.json](../.local/workspace.json) đang lưu:

```json
{
  "index": "D:\\Denso\\project\\.local\\indexes\\59f1648f8fd3"
}
```

Index này có manifest `state = ready`, `schema_version = 1.0`, **120 trang** và **1.217 chunk**; SQLite cũng chứa 1.217 record. UI lấy đường dẫn trên làm giá trị ban đầu của ô **Index hiện tại**. Nếu người dùng đã đổi ô này trong phiên đang mở, đường dẫn đang dùng có thể khác; báo cáo không đọc trạng thái widget của trình duyệt.

Trong báo cáo, `{I}` là:

```text
D:\Denso\project\.local\indexes\59f1648f8fd3
```

Trong `records.sqlite` của index này có **hai chunk chứa chuỗi `PUMP-01`**, cùng thuộc nguồn:

```text
source_relative = 12ed4abbe483_000_vietnamese.png
document_id     = doc_24f730327ee6708f
page_id         = doc_24f730327ee6708f:p0001
page_number     = null                 # nguồn là ảnh, không phải trang PDF
```

| Chunk | Nội dung hiện có |
|---|---|
| `text` | OCR tiêu đề thiết bị máy bơm, mã `PUMP-01`, áp suất `5 MPa`, điện áp `220 V`, ngày `29/09/2026`; OCR bị mất/sai một số dấu tiếng Việt |
| `page_visual` | Mô tả ảnh bằng tiếng Anh được tạo lúc build, có tên máy bơm và các thông số trên, kèm phần OCR; gắn cờ `unverified_visual_description` |

Metadata `equipment_id = PUMP-01` đang có `review_status = unreviewed`. `document_family_id` và `revision_id` chưa có giá trị. Đây là ảnh kiểm thử tổng hợp: metadata `document_type = synthetic_test`; tài liệu demo cũng ghi nhận nguồn này là fixture thử nghiệm, không thuộc corpus nghiệp vụ.

Ảnh trang đã đóng gói ở:

```text
{I}\assets\7671a84e262015ebaeb95b650b0fe6f3626d8e49d2b0d7e85a5b6f0077442184.png
```

Hai chunk trên chứng minh index có nội dung để tìm về PUMP-01. Chúng không chứng minh thứ hạng cuối cùng hoặc câu trả lời model sẽ sinh. Đáp án có thể nhận diện PUMP-01 là mã thiết bị máy bơm nếu các chunk này được chọn và model trả kết quả hợp lệ; đó là suy luận từ bằng chứng hiện có, chưa phải kết quả chạy thật.

## 3. Từng bước khi bấm Trả lời

### Bước 1 — UI nhận câu hỏi và lựa chọn

Code: [ui/app.py](../denso_a3/ui/app.py), `main()`, đặc biệt dòng 155–202.

Streamlit thực thi lại script khi xử lý thao tác. UI xác định index, kiểm tra `manifest.json`, đọc danh sách tài liệu, dựng các widget phạm vi/hội thoại/chiến lược, rồi xử lý nút **Trả lời**.

Với trường hợp chính:

```python
question = "Pump-01 là gì ?"
strategy = "auto"
filters = {}
cid = None
answer = ask(
    index,
    question,
    load_config("configs/rag.local.json"),
    strategy,
    filters,
    cid,
    None,
)
```

`load_config()` trong [rag_config.py](../denso_a3/pipeline/rag_config.py) đọc JSON cấu hình, bổ sung mặc định từ `RagConfig`, kiểm tra giá trị và chuyển `model_manifest` thành đường dẫn tuyệt đối. Cấu hình hiện tại yêu cầu embedding/reranker trên CUDA và generation local. Mặc định bổ sung gồm: `candidates = 20`, `rerank_candidates = 12`, `evidence_limit = 5`, `local_port = 8091`, `max_answer_tokens = 600`, `context_tokens = 8192`.

Nếu filters JSON sai, người dùng chọn phạm vi tài liệu nhưng chưa chọn tài liệu, hoặc chọn dài hạn mà chưa có hội thoại, UI hiển thị lỗi. Nút bị vô hiệu hóa nếu câu hỏi chỉ có khoảng trắng.

### Bước 2 — Hàm điều phối kiểm tra index

Code: [rag_runner.py](../denso_a3/pipeline/rag_runner.py), `ask()`, dòng 19.

Hàm chuyển index thành đường dẫn tuyệt đối, đọc `{I}\manifest.json`, yêu cầu `ready` và schema `1.0`, kiểm tra config, rồi đọc `{I}\registry.json`.

`Generator(config, manifest["model_manifest"])` mới tạo đối tượng adapter; chưa nạp model hay chạy server ở thời điểm này. Thông tin model dùng để hỏi lấy từ **manifest của index**, còn các lựa chọn thực thi như device, provider, port lấy từ config vừa đọc.

Điểm dễ nhầm: `load_config()` chuyển đường dẫn `.cache/rag/models.json`, nhưng luồng `ask()` hiện tại không gọi `models(config)` để đọc file đó. File này được dùng lúc build; index đã giữ một bản `model_manifest` bên trong `manifest.json`.

Ở chế độ tức thì, `search_question` giữ nguyên câu hỏi; không đọc lịch sử và không gọi model để viết lại câu hỏi.

### Bước 3 — Planner phân tích “Pump-01 là gì ?”

Code: [planner.py](../denso_a3/bt6/planner.py), `plan_query()`, dòng 17.

Planner dùng xử lý chuỗi và regex, không gọi LLM. Kết quả đã chạy trực tiếp với registry hiện tại:

```json
{
  "original_question": "Pump-01 là gì ?",
  "search_question": "Pump-01 là gì ?",
  "language": "vi",
  "strategy": "auto",
  "hard_filters": {},
  "soft_preferences": {"equipment_id": "PUMP-01"},
  "intents": ["identifier"],
  "clarification": null
}
```

Planner nhận tiếng Việt, nhận mẫu mã định danh và thấy một giá trị equipment phù hợp trong metadata. Nó chỉ thêm **ưu tiên mềm** `PUMP-01`: chunk khớp được cộng điểm sau retrieval. Nó không biến câu hỏi thành điều kiện bắt buộc `equipment_id = PUMP-01`.

Intent `identifier` hiện không có nhánh đặc biệt để ép tìm đúng mã. Với `filters = {}`, hệ thống vẫn tìm trong toàn bộ index. Câu hỏi này không kích hoạt nhánh revision, hiện hành hoặc yêu cầu làm rõ theo các regex hiện có.

### Bước 4 — Mở kho dữ liệu BT5

Code: [store.py](../denso_a3/bt5/store.py), `RetrievalStore.__init__()`, dòng 34.

Code mở SQLite `{I}\records.sqlite`, mở Qdrant local tại `{I}\vectors`, rồi đọc **toàn bộ record chunk** từ bảng `chunks` vào `store.rows` trong RAM.

`records.sqlite` có bảng `chunks` và chỉ mục toàn văn `text_index`. Qdrant giữ vector đã tạo lúc build. Hỏi đáp không nạp các chunk từ `chunks.json`; file JSON đó là artifact xuất lúc build, còn đường đọc runtime là SQLite.

### Bước 5 — Tìm bằng chứng BT6

Code: [retrieval.py](../denso_a3/bt6/retrieval.py), `retrieve()`, dòng 17.

Với `strategy = auto`, retrieval bao gồm ứng viên ảnh `page_visual` và chạy cả hai nhánh:

1. **Lexical:** `store.lexical()` tách từ trong câu hỏi; truy vấn tương đương `"Pump" OR "01" OR "là" OR "gì"`; dùng SQLite FTS5/BM25. Nó không tìm nguyên chuỗi `Pump-01` theo exact match. Bảng SQL TEMP `eligible_ids` áp dụng phạm vi trước khi xếp hạng.
2. **Dense:** tạo `TextEncoder` từ model embedding được lưu trong manifest, nạp model local, gọi `encode([search_question], query=True)`, tìm vector tương tự qua `store.dense()`/Qdrant. Vector câu hỏi chỉ dùng cho truy vấn này; không được insert vào collection.

Mỗi nhánh lấy tối đa 20 ứng viên sau loại trùng theo `content_key`; giới hạn tìm ban đầu có thể lớn hơn để bù việc loại trùng. Hai danh sách được gộp bằng điểm thứ hạng `1 / (60 + rank)` cho mỗi nhánh. Ưu tiên mềm khớp `equipment_id` cộng `0.002` nếu `metadata_boost` bật. Sau đó lấy tối đa 12 ứng viên.

Nếu còn hơn 5 ứng viên và `rerank = true`, code nạp `Reranker`, chấm từng cặp câu hỏi–nội dung chunk, sắp lại theo điểm reranker. Nếu có tối đa 5 ứng viên, rerank được bỏ qua vì tất cả đã vừa ngân sách bằng chứng. Luồng bình thường chọn tối đa 5 bằng chứng và gán `E1`, `E2`, …

`TextEncoder.close()` và `Reranker.close()` giải phóng model sau mỗi giai đoạn. Với config hiện tại, nếu CUDA không khả dụng khi cần nạp model, code báo lỗi; không tự đổi sang CPU.

### Bước 6 — Chọn nhánh ảnh và kiểm tra mâu thuẫn

Code: [rag_runner.py](../denso_a3/pipeline/rag_runner.py), dòng 66–85; [conflicts.py](../denso_a3/bt6/conflicts.py), `detect_conflicts()`, dòng 37.

Sau retrieval, nếu `auto` và **một trong hai bằng chứng đầu** có loại `page_visual` hoặc `figure`, `ask()` thêm intent `visual`. Câu hỏi mẫu không có từ khóa ảnh, nhưng vẫn có thể đi vào nhánh ảnh theo kết quả xếp hạng.

Conflict gate lấy các họ tài liệu đã xác minh từ bằng chứng rồi mở rộng sang chunk cùng họ trong phạm vi đang chọn. Nó tìm các giá trị số khác nhau có nhãn/đơn vị, cùng family và thiết bị/part, thuộc hai tài liệu khác nhau. Đây không phải bước phát hiện mọi loại mâu thuẫn bằng LLM.

Metadata PUMP-01 hiện chưa có family đã xác minh, nên bản thân hai chunk PUMP-01 không đủ điều kiện lập nhóm kiểm tra mâu thuẫn này. Nếu bằng chứng khác đưa vào một family hợp lệ, gate vẫn có thể kiểm tra family đó.

Nếu phát hiện mâu thuẫn, hệ thống trả `status = conflict`, dùng câu cảnh báo cố định và citation; không gọi model viết kết luận. Nhánh conflict có thể thay bằng chứng bằng tối đa 8 chunk liên quan, thay vì ngân sách 5 chunk của retrieval bình thường.

### Bước 7 — Chuẩn bị prompt và sinh đáp án BT7

Code: [answering.py](../denso_a3/bt7/answering.py), `grounded_answer()`, dòng 26; [generation.py](../denso_a3/backends/generation.py), `Generator`.

Khi có bằng chứng và không bị conflict, `grounded_answer()` đọc `{I}\parents.json`, tạo nội dung prompt từ các chunk đã chọn, mỗi đoạn giới hạn 3.500 ký tự. Với chunk bảng, có thể dùng toàn bảng cùng trang từ parent nếu vừa giới hạn.

Nếu `visual` bật, code lấy tối đa **hai ảnh khác nhau** từ `{I}\assets\<image_sha256>.png`, chuyển RGB, thu nhỏ tối đa 1.600 × 1.600, mã hóa PNG/base64 trong bộ nhớ để gửi cùng prompt. Nó không ghi ảnh thu nhỏ ra ổ đĩa và không chạy Docling/OCR lại. Citation sau đó vẫn trỏ tới ảnh trang đã đóng gói trong index.

Prompt yêu cầu chỉ dùng bằng chứng, giữ số/đơn vị, trả tiếng Việt, có mã dẫn nguồn và trả JSON dạng:

```json
{
  "answer": "Nội dung có dẫn nguồn [E1]",
  "status": "answered",
  "citations": ["E1"]
}
```

Đây là ví dụ **định dạng**, không phải đáp án đã chạy. Model có thể trả `abstain` nếu thấy bằng chứng chưa đủ.

`Generator.start()` kiểm tra file model/runtime và port, khởi chạy `llama-server.exe` ẩn cửa sổ, chờ `/health`, rồi `generate_json()` gửi HTTP tới:

```text
http://127.0.0.1:8091/v1/chat/completions
```

Đây là endpoint model trên máy local, khác cổng UI `8501`. Cấu hình hiện tại không gọi dịch vụ generation bên ngoài. Adapter API chỉ dùng khi người dùng đổi config sang `generation_provider = api` và cung cấp đủ cấu hình/credential.

### Bước 8 — Kiểm tra citation, trả dữ liệu, hiển thị

`citations_for()` trong [answering.py](../denso_a3/bt7/answering.py), dòng 7, kiểm tra mã citation thuộc bằng chứng được cấp, ảnh tồn tại và số trang hợp lệ. Với kết quả `answered`, phải có ít nhất một citation hợp lệ. Nếu cấu trúc hoặc citation không hợp lệ, code có thể yêu cầu model sửa một lần; lần thứ hai vẫn sai thì báo lỗi. Lỗi HTTP hoặc lỗi parse JSON từ adapter không nằm trong vòng sửa validation này.

`ask()` ghép kết quả thành `AnswerRecord`: `question`, `answer`, `status`, `language`, `citations`, `evidence`, `conflicts`, `trace`, `timings_ms`, `usage`. Đường dẫn index và chế độ memory cũng được ghi trong trace. `finally` dừng server model và đóng memory; kho SQLite/Qdrant đã đóng sau bước retrieval/conflict.

UI giữ dict đó trong `st.session_state["last_answer"]`, kèm index, mode, conversation, sources và filters. `show_answer()` hiển thị nội dung, trạng thái/thời gian, ảnh theo citation và toàn bộ JSON trong mục chi tiết. `clarification`, `conflict`, `abstain` được trình bày dạng cảnh báo; exception được UI hiển thị qua `st.error()`.

Kiểm tra citation chỉ chứng minh pointer hợp lệ, không kiểm chứng độc lập mọi mệnh đề model viết.

## 4. Những file được đọc, ở đâu và để làm gì?

Các đường dẫn bắt đầu bằng `{I}` dùng index nêu ở phần 2. Nếu đổi index trong UI, chúng đổi theo index được chọn.

| Đường dẫn | Ai đọc? | Mục đích/thời điểm |
|---|---|---|
| `D:\Denso\project\.local\workspace.json` | `ui/app.py:main` | Giá trị index ban đầu; nếu chưa có thì dùng `results/rag_main` |
| `D:\Denso\project\configs\rag.local.json` | UI và `load_config` | Hiển thị cấu hình; xác định cách chạy lượt hỏi |
| `{I}\manifest.json` | UI và `ask` | Kiểm tra ready/schema; lấy thông tin model/index |
| `{I}\documents.json` | UI | Danh sách nguồn, chọn phạm vi và dựng tab tài liệu |
| `{I}\registry.json` | UI và `ask` | Metadata của tài liệu, planner/filter/conflict |
| `{I}\sources\<hash>.<đuôi>` | UI, nếu nguồn được chọn có `source_asset` | Đọc bytes để dựng nút tải nguồn; không phải input để chạy OCR cho câu hỏi |
| `{I}\records.sqlite` | `RetrievalStore` | Đọc chunk và tìm từ khóa bằng FTS5 |
| `{I}\vectors\meta.json` | Qdrant local | Đọc mô tả collection |
| `{I}\vectors\collection\chunks\storage.sqlite` | Qdrant local | Đọc vector/payload đã lưu |
| `{I}\parents.json` | `grounded_answer`, nếu đến bước sinh đáp án | Ngữ cảnh parent/toàn bảng |
| `{I}\assets\<hash>.png` | Generator nếu visual; UI khi hiển thị citation | Ảnh bằng chứng cho model và người dùng; validation cũng kiểm tra tồn tại |
| `D:\Denso\project\.local\conversations.sqlite` | UI và `ask`, nếu dài hạn | Hội thoại, lịch sử và lưu lượt hỏi đáp |

Một chi tiết của UI: code trong các `with tabs[...]` đều chạy khi thực thi script, kể cả khi người dùng đang ở tab Hỏi đáp. Vì vậy cấu hình, tài liệu, registry và bytes của nguồn đang chọn có thể được đọc để dựng các tab khác trong cùng lượt rerun. Việc đó không kích hoạt xử lý upload hay build; các tác vụ này cần nút tương ứng.

Model/runtime được manifest của index hiện tại trỏ tới:

| Đường dẫn | Khi sử dụng |
|---|---|
| `D:\Denso\project\.cache\rag\models\BAAI--bge-m3\` | Dense retrieval: đọc tokenizer/config/weights |
| `D:\Denso\project\.cache\rag\models\BAAI--bge-reranker-v2-m3\` | Khi điều kiện rerank thỏa mãn |
| `D:\Denso\project\.cache\rag\llama\b11235\llama-server.exe` | Khi gọi generation hoặc viết lại câu hỏi |
| `D:\Denso\project\.cache\rag\models\Qwen3-VL-2B\Qwen3VL-2B-Instruct-Q4_K_M.gguf` | Model generation local |
| `D:\Denso\project\.cache\rag\models\Qwen3-VL-2B\mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf` | Vision projector; lệnh server hiện luôn truyền `--mmproj`, kể cả prompt chỉ có text |

Embedding/reranker nạp bằng `local_files_only=True`. Lượt hỏi không gọi bootstrap tải model. Thiếu model/runtime sẽ gây lỗi khi tới giai đoạn cần chúng.

## 5. File nào được tạo hoặc ghi khi chỉ gửi câu hỏi?

| Trường hợp | File hoặc tài nguyên | Điều gì xảy ra? |
|---|---|---|
| Hỏi tức thì | `st.session_state["last_answer"]` | Giữ đáp án/trace trong session của process Streamlit; không phải file trên đĩa |
| Hỏi tức thì | Vector câu hỏi, prompt, evidence, `AnswerRecord` | Tạo trong RAM; vector câu hỏi không được thêm vào index |
| Lexical retrieval | SQL TEMP table `eligible_ids` | Tạo trong connection SQLite; không phải artifact truy vấn bền vững. SQLite có thể dùng bộ nhớ hoặc vùng tạm tùy runtime |
| Mở Qdrant | `{I}\vectors\.lock` | Qdrant mở và khóa file; tạo nếu thiếu. File này đã tồn tại ở index hiện tại; không chứa câu hỏi/đáp án |
| Mở các database | File SQLite nội bộ/journal/tạm nếu runtime cần | Là chi tiết của engine. Luồng hỏi không gọi `store.add`, upsert vector hay thay metadata |
| Dài hạn | `D:\Denso\project\.local\conversations.sqlite` | Tạo DB/tables nếu chưa có; thêm conversation khi bấm Tạo hội thoại; thêm turn khi `ask` hoàn tất |
| Đổi index bằng nút Mở index | `D:\Denso\project\.local\workspace.json` | `activate()` ghi index được chọn; thao tác bấm Trả lời không gọi `activate()` |

Qdrant hiện dùng local persistence, mở SQLite theo chế độ bình thường và thực hiện `CREATE TABLE IF NOT EXISTS points`; `RetrievalStore` cũng mở SQLite theo chế độ bình thường. Do đó cần phân biệt “không ghi artifact hỏi đáp/index mới” với “mọi file đều được mở read-only”. Lượt hỏi cần truy cập khóa Qdrant; nếu một process khác đang mở cùng index, khởi tạo có thể thất bại.

Luồng UI hiện **không tự tạo** `answer.json`, `query.json`, `ocr.txt`, `bt2.json`, `bt3.json`, run mới hoặc thư mục index mới khi chỉ bấm Trả lời. `rag-ask --output <path>` có thể xuất answer JSON, nhưng đây là tùy chọn của CLI; nút Streamlit gọi `ask()` trực tiếp và không đi qua nhánh xuất file này.

Khi chạy Python/Streamlit, runtime có thể sinh cache kỹ thuật như `__pycache__` nếu cần. Chúng không phải nơi lưu lịch sử hội thoại của ứng dụng. Báo cáo liệt kê hành vi lưu trữ của code và các engine đang dùng; không phải audit toàn bộ I/O của hệ điều hành.

## 6. Index đang đọc được tạo từ trước bằng cách nào?

Manifest hiện tại ghi ba nguồn run:

```text
D:\Denso\project\results\full_200
D:\Denso\project\.local\runs\12ed4abbe483
D:\Denso\project\.local\runs\59f1648f8fd3
```

Luồng chuẩn bị dữ liệu:

```text
Tài liệu PDF/ảnh
  → BT1: inventory, render ảnh trang
  → BT2–BT3: OCR, layout, bảng; ghi run extraction
  → pipeline/index_builder.py:build_index
      → bt4/chunking.py:build_chunks
      → bt4/metadata.py:create_registry
      → caption ảnh nếu bật
      → TextEncoder.encode cho nội dung tài liệu
      → bt5/store.py:RetrievalStore.add
      → snapshot index ready
  → các câu hỏi sau đọc snapshot đó
```

Với upload UI, file gốc nằm trong `.local/uploads/<token>/`, extraction trong `.local/runs/<token>/`, index trong `.local/indexes/<token>/`. `build_index()` tạo thư mục staging `<token>.building-<id>`, đóng gói ảnh và nguồn, ghi SQLite/Qdrant cùng các JSON rồi rename thành snapshot cuối. Caption cache, nếu có caption mới, được ghi dưới `.cache/rag/captions/`.

`chunks.json`, `pages.json`, `documents.json`, `registry.json`, `parents.json`, `manifest.json`, `records.sqlite`, `vectors/`, `assets/`, `sources/` là các artifact đã được chuẩn bị. Chúng giúp câu hỏi không phải xử lý lại tài liệu từ đầu. Xem [báo cáo upload](UI_UPLOAD_CODE_FLOW.md) để đọc đầy đủ luồng tạo file trước khi hỏi.

## 7. Điều gì thay đổi với lựa chọn khác?

### Hội thoại dài hạn

Code: [memory.py](../denso_a3/bt7/memory.py), `ConversationStore`; [rag_runner.py](../denso_a3/pipeline/rag_runner.py), dòng 32–44 và 97–103.

UI mở `.local/conversations.sqlite` để liệt kê và hiển thị lịch sử. `create()` thêm một dòng vào bảng `conversations`; `append()` thêm một dòng vào bảng `turns`. Một turn chứa `conversation`, `scope`, `question`, `answer`; cột `answer` là chuỗi JSON của toàn bộ `AnswerRecord`, có cả citation/evidence/trace.

Trong `ask()`, scope là fingerprint của đường dẫn index và filters. Hàm lấy 6 lượt gần nhất của conversation rồi lọc scope; nếu có lịch sử phù hợp, dùng tối đa 3 lượt cuối để gọi model viết lại câu hỏi thành câu độc lập. Vì lọc scope diễn ra sau SQL LIMIT, lịch sử cũ hơn giới hạn có thể không được lấy dù có scope phù hợp.

Câu đầu của hội thoại mới không cần rewrite. Câu nối tiếp như “Nó dùng điện áp bao nhiêu?” có thể được rewrite thành câu nêu PUMP-01 rồi đi tiếp planner/retrieval. Câu trả lời cũ chỉ là ngữ cảnh tham chiếu, không được coi là bằng chứng tài liệu.

Nếu `ask()` trả được kết quả, kể cả `abstain`, `conflict`, `clarification`, turn được lưu. Nếu exception làm hàm không đi tới `append()`, lượt lỗi đó không được lưu như một answer hoàn tất. Rewrite và generation có thể làm server model được khởi động hai lần trong một lượt vì code đóng generator sau rewrite để nhường giai đoạn embedding/rerank.

### Chọn tài liệu hoặc hard filters

Chọn **Chọn tài liệu** thêm `filters["source_relative"] = [...]`. Các retriever đều áp dụng điều kiện này trước giới hạn kết quả.

Filter nghiệp vụ như `{"equipment_id":"PUMP-01"}` chỉ khớp metadata `user_verified` hoặc `source_verified`. Vì equipment của ảnh kiểm thử đang `unreviewed`, filter này sẽ loại hai chunk đó. Khác với hard filter, ưu tiên mềm do planner suy ra vẫn có thể cộng điểm cho metadata chưa verified. Không có cơ chế tự bỏ hard filter khi không tìm được kết quả.

### Đổi chiến lược

| Chiến lược | Hành vi trong code hiện tại |
|---|---|
| `auto` | Lexical + dense; cho phép ứng viên ảnh; tự bật gửi ảnh nếu top 2 có visual/figure |
| `lexical` | Bỏ dense embedding; vẫn có thể rerank và generation nếu có bằng chứng |
| `dense` | Bỏ lexical; vẫn có thể rerank và generation |
| `hybrid` | Lexical + dense; không có điều kiện tự bật visual theo top 2 như `auto` |
| `visual` | Planner thêm intent visual; retrieval vẫn lexical + dense trên text/caption; generation kèm tối đa hai ảnh |

Đối với câu hỏi mẫu, nếu chọn `hybrid` thay `auto`, không có intent visual từ từ khóa, nên `page_visual` bị loại khỏi tập ứng viên. Figure là một loại chunk khác: điều kiện loại trong `allowed()` chỉ xét `page_visual`. Nhánh visual hiện không phải một hệ thống embedding ảnh riêng; nó tìm qua text/caption rồi cho VLM xem ảnh khi sinh đáp án.

## 8. Các điểm code để tự lần theo

| Điểm vào | File và dòng bắt đầu |
|---|---|
| Streamlit main, nhận câu hỏi và xử lý nút | `denso_a3/ui/app.py:53`, khu vực câu hỏi `:182`, gọi `ask` `:193` |
| Hiển thị đáp án/citation | `denso_a3/ui/app.py:36` |
| Nạp cấu hình | `denso_a3/pipeline/rag_config.py:9` |
| Điều phối toàn bộ lượt hỏi | `denso_a3/pipeline/rag_runner.py:19` |
| Hợp đồng config/answer/plan | `denso_a3/contracts.py:82`, `:65`, `:38` |
| Planner | `denso_a3/bt6/planner.py:17` |
| Mở kho và filter | `denso_a3/bt5/store.py:33`, `eligible` `:16` |
| Lexical/dense | `denso_a3/bt5/store.py:71`, `:85` |
| Điều phối retrieval | `denso_a3/bt6/retrieval.py:17` |
| Embedding/reranker | `denso_a3/backends/rag_models.py:9`, `:66` |
| Conflict gate | `denso_a3/bt6/conflicts.py:37` |
| Sinh đáp án và citation | `denso_a3/bt7/answering.py:26`, `:7` |
| Model server và HTTP | `denso_a3/backends/generation.py:28`, `start` `:37`, `generate_json` `:72` |
| Lưu hội thoại | `denso_a3/bt7/memory.py:8` |
| Tạo index trước khi hỏi | `denso_a3/pipeline/index_builder.py:43` |

Các số dòng được đối chiếu với source tại thời điểm viết; đường dẫn trong bảng tương đối với `D:\Denso\project`. Trong process đang chạy, Python import các module/định nghĩa hàm; “gọi file” trong báo cáo có nghĩa là gọi hàm/class được định nghĩa trong file đó, không phải chạy lại từng file như một chương trình độc lập. Subprocess cụ thể là Streamlit lúc khởi động UI và llama-server lúc generation; OCR/build có subprocess riêng khi người dùng chọn các tác vụ tương ứng.
