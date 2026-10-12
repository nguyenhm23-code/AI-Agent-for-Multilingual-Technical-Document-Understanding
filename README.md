# AI Agent for Multilingual Technical Document Understanding

**Đọc PDF/ảnh kỹ thuật → tìm bằng chứng → trả lời có dẫn nguồn bằng model local.**

Dự án `denso_a3` kết hợp extraction BT1–BT3 và RAG BT4–BT7. Người dùng đặt câu hỏi
bằng Anh/Việt, chọn phạm vi tài liệu và xem câu trả lời cùng ảnh nguồn. Hệ thống tự
chọn luồng baseline hoặc hierarchical, đọc evidence ảnh khi cần và kiểm chứng đáp án
trước khi hiển thị. [Mã điều phối](denso_a3/pipeline/rag_runner.py) ·
[UI](denso_a3/ui/app.py) · [Cấu hình mặc định](configs/rag.local.json).



| Đầu vào | Xử lý | Đầu ra |
|---|---|---|
| PDF, PNG/JPG/TIFF và các ảnh được CLI hỗ trợ | OCR, layout/bảng, chunking, hybrid retrieval, VLM và verification | Artifact extraction, index RAG, đáp án JSON và citation tới file/trang/ảnh |
| Câu hỏi Anh/Việt + phạm vi tài liệu | Auto routing baseline/hierarchical; phép tính dùng Decimal khi đủ fact | `answered`, `clarification`, `abstain` hoặc `conflict` |

**Mục lục:** [Kiến trúc](#architecture) · [Luồng dữ liệu](#data-flow) ·
[Docker-quickstart](#docker-quickstart) · [Demo mẫu](#sample-demo) ·
[Ảnh kết quả](#demo-results) · [Kiểm chứng và giới hạn](#validation) ·
[Xử lý sự cố](#troubleshooting) · [Tài liệu kỹ thuật](#technical-reference).

<a id="architecture"></a>

## 1. Kiến trúc hệ thống

```mermaid
flowchart TB
    USER["Người dùng: câu hỏi + phạm vi tài liệu"]
    INPUT["PDF / ảnh kỹ thuật"]
    subgraph APP["Một Docker image: denso-pipeline:local"]
        UI["Streamlit UI / CLI"]
        BT1["BT1 · Ingestion và ảnh trang canonical"]
        BT23["BT2–BT3 · Docling: OCR, layout, bảng"]
        BT4["BT4 · Metadata, chunks, hierarchy"]
        BT5["BT5 · SQLite FTS5 + Qdrant embedded"]
        BT6["BT6 · Scope, routing, hybrid retrieval"]
        BT7["BT7 · Evidence ảnh, fact, tính toán, đáp án"]
        CHECK["Kiểm chứng từng ý bằng evidence được phép"]
        MODELS["Adapters model local · llama-server nội bộ"]
    end
    USER --> UI
    INPUT --> BT1 --> BT23 --> BT4 --> BT5
    UI --> BT6
    BT5 --> BT6 --> BT7 --> CHECK
    CHECK --> RESULT["Đáp án + citation + trace / yêu cầu làm rõ"]
    RESULT --> UI
    MODELS -.-> BT23
    MODELS -.-> BT4
    MODELS -.-> BT6
    MODELS -.-> BT7
    MODELS -.-> CHECK
```

BT2 và BT3 nhận kết quả từ **cùng một lần Docling conversion cho mỗi trang**.
Embedding, reranking và generation nạp/đóng theo từng stage để hạn chế VRAM;
đường nối trong sơ đồ thể hiện trao đổi dữ liệu, không có nghĩa các model chạy đồng thời.
[Mã extraction](denso_a3/pipeline/runner.py) · [Adapter Docling](denso_a3/backends/docling_backend.py).

| Khối | Trách nhiệm | Model / storage |
|---|---|---|
| BT1 | Đọc nguồn, gán ID/hash, tạo input trang và tọa độ nguồn | PyMuPDF + ảnh trang canonical |
| BT2–BT3 | OCR, layout, text spans, cấu trúc bảng | RapidOCR Torch + layout Heron + TableFormer accurate |
| BT4 | Chunks có nguồn, gợi ý metadata chờ xác nhận, document/page hierarchy | Chunking theo bảng; caption ảnh hỗ trợ tìm kiếm |
| BT5 | Lưu snapshot, tìm keyword và vector | SQLite FTS5 + Qdrant embedded |
| BT6 | Chuẩn hóa câu hỏi/scope, auto routing, retrieval và reranking | BAAI/bge-m3 + BAAI/bge-reranker-v2-m3 |
| BT7 + orchestration | Đọc evidence ảnh, fact validation, Decimal, tổng hợp và verification | Qwen3-VL-2B GGUF + llama.cpp local; memory SQLite |

Caption ảnh và metadata gợi ý **chưa phải fact đã xác minh**. Hard filters chỉ dùng
metadata có trạng thái phù hợp; ảnh nguồn được đọc trong visual phase khi cần.
[Kiến trúc chi tiết](docs/3-10/SYSTEM_ARCHITECTURE.md) · [Shared contracts](denso_a3/contracts.py).

<a id="data-flow"></a>

## 2. Luồng dữ liệu

### Tạo dữ liệu và index

```mermaid
flowchart LR
    SOURCE["evaluation/data: PDF / ảnh"] --> INGEST["BT1 · IDs, hash, PageInput"]
    INGEST --> EXTRACT["Docling OCR + layout + bảng"]
    EXTRACT --> ART["extraction/: documents, pages, BT2, BT3"]
    ART --> CHUNK["Chunks + metadata + hierarchy"]
    CHUNK --> EMBED["Embedding và routing vectors"]
    EMBED --> STAGE["Snapshot staging + assets nguồn"]
    STAGE --> READY["index/manifest.json: ready"]
    ART --> REVIEW["review.html + ảnh/bbox để đối chiếu"]
```

`container_cli → run → rag-build` ghi extraction và index vào **output mới**.
Index chỉ được query khi manifest là `ready`. Lỗi ở một stage dừng luồng và giữ artifacts
để debug. Text/bảng giữ source ID, trang và bbox; bbox citation chuẩn hóa
`[x0,y0,x1,y1]` theo ảnh trang, gốc trái trên. PDF đánh số trang từ 1; ảnh độc lập có
`page_number=null`. [Builder](denso_a3/pipeline/index_builder.py) · [Artifacts](denso_a3/backends/artifacts.py).

### Hỏi đáp và kiểm chứng

```mermaid
flowchart TB
    Q["Câu hỏi + tài liệu được chọn"] --> SCOPE["Local policy + scope + memory/pending"]
    SCOPE --> PLAN["Phân tích yêu cầu và auto router"]
    PLAN -->|"Tra cứu trực tiếp"| BASE["Baseline · hybrid leaf retrieval"]
    PLAN -->|"Nhiều bước / phép tính"| RESEARCH["Hierarchical · document/page routing + tasks"]
    BASE --> EVIDENCE["Evidence text / bảng / ảnh trong scope"]
    RESEARCH --> EVIDENCE
    EVIDENCE --> VISUAL["VLM đọc crop ảnh khi cần"]
    VISUAL --> DRAFT["Facts, coverage, Decimal nếu cần → đáp án có nguồn"]
    DRAFT --> VERIFY["Verification độc lập của từng ý và câu hỏi"]
    VERIFY -->|"Đủ hỗ trợ"| ANSWER["answered + citations + trace"]
    VERIFY -->|"Thiếu / mâu thuẫn / lỗi"| STOP["abstain / conflict hoặc lỗi rõ ràng"]
    PLAN -->|"Thiếu phạm vi / cần lựa chọn"| CLARIFY["clarification"]
    ANSWER --> MEMORY["Một turn final; lưu memory khi chọn dài hạn"]
    STOP --> MEMORY
    CLARIFY --> MEMORY
```

Baseline có thể escalation sang hierarchical tối đa một lần khi còn budget và có hướng
bổ sung evidence. Mọi bước giữ phạm vi tài liệu/filters ban đầu. Verification đánh giá
hỗ trợ từ nguồn; nó không phải xác suất đúng của OCR hoặc model.
[Baseline](denso_a3/pipeline/baseline_runner.py) · [Research](denso_a3/pipeline/research_runner.py).

<a id="docker-quickstart"></a>

## 3. Chạy nhanh bằng Docker 

### Chuẩn bị

- Mở terminal tại thư mục gốc có [Dockerfile](Dockerfile) và [compose.yaml](compose.yaml).
- Dùng Docker với **Linux containers, x86_64, GPU NVIDIA** và driver tương thích CUDA 12.8.
  Windows dùng Docker Desktop với WSL2; xem [hướng dẫn GPU chính thức của Docker](https://docs.docker.com/desktop/features/gpu/).
- Lần đầu cần mạng để build và tải model. Image đã kiểm thử khoảng **19,7 GB**;
  cần thêm dung lượng cho weights, build cache và output. Thời gian tùy mạng/máy.
- Máy đã kiểm thử: RTX 4050 Laptop GPU, 6 GB VRAM. Đây là cấu hình demo đã chạy,
  chưa phải cam kết về mọi GPU, corpus hoặc mức tải đồng thời.

### Build, tải model và kiểm tra GPU

Các lệnh dưới đây dùng được trong PowerShell hoặc shell Linux, chạy từ thư mục gốc repo.
Không cần cài Python trên máy chủ để chạy Docker.

```bash
# 1. Build image duy nhất. Nếu đã nhận image và có tag này, có thể bỏ qua build.
docker compose build

# 2. Lần đầu: tải model OCR tiếng Anh và model RAG vào volume.
docker compose run --rm pipeline bootstrap-all --language en

# 3. Kiểm tra model/device offline sau bootstrap.
docker compose run --rm pipeline doctor
```

Kết quả bước 3 cần có `device_requested=cuda`, các `ocr_devices` và `stage_devices`
ở `cuda:0`. Bootstrap thiếu model hoặc GPU sẽ báo lỗi rõ; query không tự tải model
và không tự chuyển cloud. Nếu BTC nhận image dưới dạng tar:

```bash
docker load -i denso-pipeline.tar
# Image phải có tag denso-pipeline:local, sau đó chạy bootstrap/doctor như trên.
```

<a id="sample-demo"></a>

### Demo tái hiện một tài liệu có sẵn trong repo

Tài liệu [images/24580.png](evaluation/data/images/24580.png) được version trong repo.
Câu hỏi tương ứng và đáp án tham chiếu nằm trong [dataset_test.json](evaluation/data/dataset_test.json).

```bash
# Dừng UI trước khi chạy CLI với cùng index/GPU.
docker compose stop pipeline

# OCR → tạo index → hỏi đáp, tất cả dùng cùng một image.
# Mỗi lần chạy đổi output sang thư mục mới, ví dụ btc_demo_002.
docker compose run --rm pipeline pipeline --output /app/results/btc_demo_001 --source images/24580.png --limit 1 --question "What is the name of the university?"

# Khởi động giao diện.
docker compose up -d
```

Mở **[http://127.0.0.1:8501](http://127.0.0.1:8501)** rồi:

1. Trong sidebar, nhập `/app/results/btc_demo_001/index` vào **Index hiện tại**, bấm **Mở index**.
2. Ở tab **Hỏi đáp**, chọn **Chọn tài liệu** → `images/24580.png`.
3. Nhập `What is the name of the university?`, bấm **Trả lời**.
4. Mở mục kiểm chứng và citation để đối chiếu tên trường trên ảnh nguồn.

| File trên máy chủ | Kết quả mong đợi |
|---|---|
| `results/btc_demo_001/extraction/bt2.json`, `bt3.json` | BT2 và BT3 `ok` cho trang mẫu |
| `results/btc_demo_001/extraction/performance.json`, `runtime.json` | Timing/device của extraction; CLI in startup error nếu có |
| `results/btc_demo_001/extraction/review.html` | Review OCR/layout/bảng cùng nguồn để kiểm tra |
| `results/btc_demo_001/index/manifest.json` | `state=ready` |
| `results/btc_demo_001/answer.json` | `status=answered`, tên trường California/San Diego, citations và `verification.state=verified` |

Đáp án/thời gian có thể thay đổi giữa các lần chạy. Tiêu chí đối chiếu là nguồn và status,
không phải chuỗi đáp án hoặc số giây giống hệt ảnh demo.

### Tự thử tài liệu của BTC

Trong UI, vào **Tài liệu & upload**, thêm PDF/ảnh, chọn ngôn ngữ OCR rồi bấm
**Xử lý upload và tạo index mới**. Khi hoàn tất, tải lại trang để mở index mới;
chọn tài liệu trước khi hỏi. Mỗi file upload tối đa 50 MB theo UI hiện tại.

OCR tiếng Việt cần tải thêm bộ OCR trước khi upload:

```bash
docker compose stop pipeline
docker compose run --rm pipeline doctor --language vi --download-models
docker compose up -d
```

Với CLI, đặt file vào `evaluation/data/`, dùng `--source` là đường dẫn **tương đối**
với thư mục input và `--language vi` nếu cần. Bỏ `--limit` để xử lý toàn bộ trang nguồn;
bỏ `--question` nếu chỉ cần extraction và index.

### Model, dữ liệu và việc dừng ứng dụng

```mermaid
flowchart LR
    HOST["Máy chủ · GPU NVIDIA"] --> CONTAINER["Một container · UI + CLI + model adapters"]
    INPUT["evaluation/data trên máy chủ"] -->|"read-only"| CONTAINER
    CONTAINER --> OUTPUT["results trên máy chủ: extraction, index, answer"]
    CACHE["model-cache volume: /app/.cache"] <--> CONTAINER
    STATE["app-data volume: /app/.local"] <--> CONTAINER
    BROWSER["Browser · 127.0.0.1:8501"] <--> CONTAINER
```

```bash
docker compose logs -f pipeline
docker compose stop
# Khởi động lại, dữ liệu trong volumes và results vẫn còn.
docker compose up -d
```

**Image không chứa weights.** Chuyển image sang máy khác cần bootstrap lại hoặc chuyển
model volume phù hợp. Không mount cache/index Windows cũ trực tiếp vào Linux: manifest có
thể chứa đường dẫn Windows. Không dùng `docker compose down -v` nếu muốn giữ named volumes.
Qdrant embedded cho một process mở cùng index; không chạy nhiều CLI/UI job cùng index.

<a id="demo-results"></a>

## 4. Ảnh chụp kết quả thực tế

Ảnh dưới đây chụp ngày **12/10/2026** từ Streamlit thật chạy trong image Docker,
không phải mockup. Demo sử dụng dataset tự chuẩn bị, scope cụ thể và hội thoại tức thì.

| Câu hỏi, đáp án và verification | Mở citation để đối chiếu ảnh nguồn |
|:---:|:---:|
| ![UI thật: câu hỏi tên trường, đáp án và trạng thái kiểm chứng](docs/readme/assets/ui-answer.jpg) | ![UI thật: claim được hỗ trợ và ảnh nguồn có tên trường](docs/readme/assets/ui-evidence.jpg) |

Đáp án hiển thị **UNIVERSITY OF CALIFORNIA, SAN DIEGO [E2] [V1]**;
UI ghi `answered`, đã đối chiếu với nguồn, route baseline/direct_lookup.
Lượt UI trong ảnh khoảng **23,1 giây**; lượt CLI trước đó khoảng **20 giây**.
Cả hai số gồm chi phí nạp model của lượt thử, không đại diện benchmark toàn corpus.

Các artifacts local của lượt CLI nằm trong `results/docker_smoke_scoped_20261012/`.
`results/` được gitignore; trên checkout mới BTC cần chạy demo để tạo lại chúng.
Hai ảnh minh họa trong `docs/readme/assets/` được đưa vào tài liệu để README vẫn xem được.

<a id="validation"></a>

## 5. Kiểm chứng, giới hạn và vòng phản hồi

| Đã kiểm chứng | Bằng chứng / phạm vi |
|---|---|
| Image Linux build và runtime llama.cpp | Image local có code BT1–BT7; runtime Linux b11235 được kiểm tra SHA-256 và chạy `--version` |
| GPU thực tế | PyTorch nhận RTX 4050; `doctor` offline xác nhận OCR, layout và TableFormer trên CUDA |
| Liên kết giữa các stage | CLI `pipeline` chạy OCR → index → hỏi đáp trên tài liệu mẫu; `--source` truyền xuyên suốt |
| Đáp án có nguồn | `answered`, hai citations, verification `verified`; tên trường đối chiếu trực tiếp trên ảnh |
| Regression Linux | 20 tests của container CLI và RAG interfaces pass; tests dùng mocks cho các contracts |
| Persistence | Restart container giữ model cache, UI state và index `ready`; health endpoint trả HTTP 200 |

**Suy luận có giới hạn:** kết quả trên xác nhận luồng triển khai của ca demo đã chạy;
không đủ để kết luận đúng/nhanh trên mọi tài liệu. Weights trong lượt thử được tái sử dụng
vào volume Linux mới, nên chưa xác nhận download toàn bộ weights từ cache rỗng. OCR có thể
đọc sai chữ viết tay/số/đơn vị; `verified` đánh giá hỗ trợ từ evidence, không bảo đảm nguồn
hoặc quan sát ảnh luôn đúng. Chưa có A/B/C human-reviewed toàn corpus để khẳng định
kiến trúc nào tốt hơn. [Báo cáo hiện có](docs/3-10/ANSWER_VERIFICATION_METADATA_REPORT.md).

BTC có thể xác minh theo vòng phản hồi sau:

1. **Tái hiện:** chạy demo trên một output mới; giữ question, config, source và phiên bản model.
2. **Đối chiếu:** kiểm tra `answer.json`, mở citation và ảnh nguồn; đọc `review.html` để tìm lỗi OCR.
3. **Phân loại:** nếu OCR sai, xem extraction; nếu thiếu evidence, xem retrieval/scope;
   nếu verifier bác bỏ, đọc claim verdict và support refs; thiếu thông tin phải clarification/abstain.
4. **Sửa và thử lại:** chỉnh input/scope/config thích hợp, tạo snapshot mới, giữ artifacts cũ để so sánh.
5. **Mở rộng:** dùng tài liệu chưa thấy trước và gold do người review, đo latency và chất lượng;
   tránh dùng một smoke test để suy rộng độ chính xác.

Có thể chạy tests trong image từ PowerShell tại thư mục gốc:

```powershell
docker run --rm --entrypoint python -v "${PWD}/tests:/app/tests:ro" denso-pipeline:local -m pytest tests/test_container_cli.py tests/test_rag_interfaces.py -q
```

<a id="troubleshooting"></a>

## 6. Xử lý sự cố nhanh

| Hiện tượng | Kiểm tra / cách xử lý |
|---|---|
| Docker CLI không kết nối được Engine | Mở Docker Desktop, xác nhận Linux Engine chạy; dùng `docker version` để kiểm tra Server |
| `CUDA requested but unavailable` | Kiểm tra NVIDIA driver, GPU passthrough và WSL2 trên Windows; chạy lại `doctor` |
| `Local model/runtime missing` hoặc thiếu OCR assets | Chạy `bootstrap-all`/bootstrap đúng ngôn ngữ với mạng, kiểm tra model volume |
| Output/index đã tồn tại | Đổi `--output` sang thư mục mới; snapshots không được ghi đè |
| Qdrant lock hoặc GPU thiếu bộ nhớ | Dừng UI/job khác, chỉ để một process dùng cùng index; không xóa lock đang được sử dụng |
| Hệ thống hỏi “Which document do you mean?” | Chọn tài liệu trong UI hoặc truyền `--source` trong CLI |
| Trả `abstain`/`conflict` | Đọc evidence, verification và trace; đây có thể là kết quả đúng khi nguồn thiếu/mâu thuẫn |
| UI không mở tại cổng 8501 | Xem `docker compose ps`, `docker compose logs pipeline`; kiểm tra `http://127.0.0.1:8501/_stcore/health` |

<a id="technical-reference"></a>

## 7. Mã nguồn và tài liệu kỹ thuật

| Cần xem | Đường dẫn |
|---|---|
| Image và lưu trữ | [Dockerfile](Dockerfile), [compose.yaml](compose.yaml), [runtime installer](docker/install_runtime.py) |
| Entry point Docker | [container_cli.py](denso_a3/pipeline/container_cli.py) |
| CLI và điều phối | [cli.py](denso_a3/pipeline/cli.py), [rag_cli.py](denso_a3/pipeline/rag_cli.py), [rag_runner.py](denso_a3/pipeline/rag_runner.py) |
| Contracts và cấu hình | [contracts.py](denso_a3/contracts.py), [rag.local.json](configs/rag.local.json) |
| Kiến trúc, luồng gọi, so sánh runner | [System architecture](docs/3-10/SYSTEM_ARCHITECTURE.md), [Demo flow](docs/3-10/DEMO_CODE_FLOW.md), [Comparison](docs/3-10/ARCHITECTURE_COMPARISON.md) |
| Verification và metadata | [Báo cáo](docs/3-10/ANSWER_VERIFICATION_METADATA_REPORT.md) |
| Đánh giá để làm slide | [Hướng dẫn](docs/SLIDE_EVALUATION_GUIDE.md) |

Phần dưới giữ hướng dẫn local, contracts, input/output, benchmark, migration và báo cáo
trước đó. Các con số kiểm chứng lịch sử giữ ngày và phạm vi tương ứng.

<details>
<summary><strong>Mở hướng dẫn kỹ thuật đầy đủ: Docker, local setup, contracts và đánh giá</strong></summary>

## Chạy toàn bộ pipeline bằng một Docker image

`Dockerfile` tạo image `denso-pipeline:local` cho Linux x86_64, Python 3.12 và CUDA 12.8.
Image chứa code BT1–BT7, Streamlit, thư viện Python và `llama-server` Linux từ release chính thức
llama.cpp `b11235` (CUDA 12.8), được xác thực SHA-256. Máy chạy cần GPU NVIDIA tương thích và Docker có GPU passthrough;
Windows dùng Docker Desktop Linux containers với WSL2. Driver NVIDIA vẫn cài trên máy chủ.

**Image không chứa model weights hoặc tài liệu riêng.** Bootstrap tải weights vào volume
một lần, sau đó OCR/query dùng model local. Khi chuyển máy cần chuyển cả volume model
hoặc bootstrap lại; chỉ chuyển image chưa đủ để chạy offline. Không mount `.cache` hoặc
`.local` Windows cũ vào container: manifest/runtime và index có thể chứa đường dẫn Windows.
Docker build không cần GPU, nhưng bootstrap OCR và chạy pipeline theo cấu hình mặc định cần GPU.

```powershell
# Tạo image duy nhất; lần đầu phải tải dependency và runtime llama.cpp CUDA.
docker compose build

# Tải model OCR tiếng Anh và RAG, đồng thời kiểm tra GPU thật.
docker compose run --rm pipeline bootstrap-all --language en
# Nếu cần OCR tiếng Việt, tải thêm đúng bộ OCR (RAG không cần tải lại).
docker compose run --rm pipeline doctor --language vi --download-models

# Đặt tài liệu trong evaluation/data. Mỗi lần chạy chọn output mới.
# OCR -> index RAG -> hỏi đáp; bỏ --question nếu chỉ cần extraction + index.
docker compose run --rm pipeline pipeline --output /app/results/docker_run_001 --source images/24580.png --limit 1 --question "What is the name of the university?"

# UI tại http://127.0.0.1:8501, upload tài liệu hoặc chọn index theo luồng UI.
docker compose up -d
docker compose logs -f
docker compose stop
```

`compose.yaml` chỉ khai báo **một service dùng một image**, không cần Qdrant server hay
model server riêng. `llama-server` được ứng dụng khởi động nội bộ khi cần. Named volumes
`model-cache` và `app-data` giữ `/app/.cache` và `/app/.local`; `./results` trên máy chủ
giữ output; `./evaluation/data` được mount read-only. Không dùng `docker compose down -v`
nếu cần giữ weights/index/history. Qdrant embedded vẫn chỉ cho một process mở cùng index:
dừng UI trước khi dùng CLI thao tác cùng index, và tránh chạy nhiều job GPU đồng thời.

Entry point là `python -m denso_a3.pipeline.container_cli`:

- `bootstrap-all --language en|vi` gọi `doctor --download-models` rồi `rag-bootstrap`;
  có tải mạng, ghi cache và `models.json`, dừng ngay nếu một bước trả mã lỗi.
- `pipeline --input <folder> --output <new-folder> [--language vi] [--source <relative-path>]
  [--limit <pages>] [--config <json>] [--question <text>]` gọi `run` → `rag-build` →
  `rag-ask` nếu có câu hỏi. Mỗi bước là process riêng, chờ kết thúc trước bước tiếp theo.
  `--source` áp dụng cả cho extraction lẫn scope hỏi đáp. Khi xử lý cả corpus, bỏ
  `--source`/`--limit` và nêu phạm vi rõ trong câu hỏi; câu thiếu scope có thể yêu cầu làm rõ.
  Output gồm `extraction/` (artifacts OCR, bảng, review), `index/` (snapshot RAG), và
  `answer.json` khi có câu hỏi. Không ghi đè output tồn tại; bước lỗi giữ artifacts để
  debug và trả nguyên exit code. Answer có thể `abstain`/`clarification` dù lệnh thành công.
- Các lệnh khác chuyển nguyên tham số tới CLI hiện có: `doctor`, `run`, `rag-build`,
  `rag-ask`, `rag-evaluate`, `rag-ui`… Dữ liệu đầu vào/đầu ra, bbox chuẩn hóa và số trang
  giữ nguyên contracts được mô tả bên dưới. Các file benchmark ngoài image phải được mount.
- `rag-ui --host 0.0.0.0` bind bên trong container; Compose chỉ publish cổng trên
  `127.0.0.1` của máy chủ. Lệnh local `rag-ui` vẫn mặc định bind `127.0.0.1`.

Bootstrap trong image dùng `DENSO_LLAMA_SERVER=/opt/llama/llama-server`, kiểm tra executable,
ghi đường dẫn và SHA-256 vào manifest; không tải `.exe` Windows. Windows ngoài Docker vẫn
giữ cách bootstrap cũ. Image áp dụng các phiên bản trong lock làm constraints, bỏ
`pywin32` và URL wheel Windows của torch/torchvision, cài CUDA wheels Linux từ PyTorch.
Linux có thể bổ sung dependency theo platform; đây chưa phải lock Linux có hashes đầy đủ.
`.dockerignore` dùng allowlist để không gửi venv, weights, tài liệu và secrets ở thư mục gốc
vào build context. Runtime dùng binary Linux x64 CUDA 12.8 chính thức; không cần
compile CUDA khi build image. `docker/install_runtime.py` giữ URL và checksum cố định.

Không dùng Compose vẫn chạy được cùng image, ví dụ UI sau khi đã bootstrap vào các volume
cùng tên bên dưới (volume của Compose có project prefix và là các volume khác):

```powershell
docker run --rm --gpus all --init -v denso-models:/app/.cache -v denso-state:/app/.local denso-pipeline:local bootstrap-all
docker run --rm --gpus all --init -p 127.0.0.1:8501:8501 -v denso-models:/app/.cache -v denso-state:/app/.local denso-pipeline:local
```

Kiểm chứng implementation: tests của container CLI dùng mocks để kiểm tra thứ tự stages,
dừng khi lỗi, liên kết paths, chống ghi đè, bootstrap runtime và địa chỉ UI. Chúng không
thay thế build image hoặc inference Linux/GPU thật. Sau build cần chạy bootstrap, một
pipeline nhỏ với `--limit 1`, kiểm tra citation từ tài liệu gốc, rồi khởi động lại container
để xác nhận persistence.

Kiểm chứng Docker ngày 12/10/2026: image Linux build thành công; PyTorch nhận GPU RTX 4050;
Docling/Qdrant/Streamlit import thành công; `doctor` offline xác nhận OCR, layout và
TableFormer đều trên `cuda:0`. Hai mươi regression tests CLI pass trong Linux. Tài liệu
`images/24580.png` qua BT2/BT3 `ok`, tạo index hierarchy `ready`, hỏi đáp có source trả
`UNIVERSITY OF CALIFORNIA, SAN DIEGO`, hai citations, verification `verified` (~20,8 giây
query trong lượt thử). Weights được tái sử dụng từ cache dự án vào volume mới, RAG bootstrap
tạo manifest Linux; đây không phải phép thử download toàn bộ weights từ cache rỗng hay
đánh giá độ chính xác toàn corpus. Artifacts lượt đầu ở `results/docker_smoke_20261012/`;
lượt pipeline liền mạch sau khi sửa truyền source ở `results/docker_smoke_scoped_20261012/`,
có `answer.json` trạng thái `answered`, verification `verified` và hai citations (~20 giây query).
Image cuối `denso-pipeline:local` khoảng 19,7 GB, không gồm weights trong volumes. UI
trả HTTP 200 tại `/_stcore/health`; restart container giữ nguyên model cache, UI state
và index `ready`. Kho Docker trên máy kiểm thử đã chuyển sang
`D:\DockerData\DockerDesktopWSL`; đây là cấu hình máy chủ, không phải yêu cầu của image.

## Chạy RAG local tự định tuyến

Sau phần cài môi trường bên dưới:

```powershell
$env:PYTHONUTF8 = '1'
& ./.venv/Scripts/python.exe -m denso_a3 rag-bootstrap
& ./.venv/Scripts/python.exe -m denso_a3 rag-build --run results/full_200 --output results/rag_main --config configs/rag.local.json
& ./.venv/Scripts/python.exe -m denso_a3 rag-ui
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index results/rag_main --source images/24580.png --question "What is the name of the university?" --config configs/rag.local.json
```

Bootstrap tải weights/runtime một lần. Query dùng model đã có; không tự download, không chuyển cloud khi local lỗi. UI phục vụ máy local tại `http://127.0.0.1:8501`, một người dùng. Weights/runtime ở `.cache/rag`, dữ liệu UI/memory ở `.local`, run/benchmark ở `results`. Qdrant local chỉ cho một process mở cùng index tại một thời điểm. Upload tiếng Việt cần bootstrap OCR bằng `doctor --language vi --download-models`; OCR và đọc ảnh còn phải đối chiếu nguồn.

`configs/rag.local.json` đặt `orchestration_mode=auto`, `retrieval_strategy=hybrid`, `visual_policy=auto`, `local_only=true`. UI dùng `dataclasses.replace` để áp dụng bốn giá trị này mà không sửa object config đã tải. UI giữ hard filters nâng cao, nguồn/citation và instant/long-term memory; không có selector kiến trúc/strategy hoặc form giả định JSON.

Build mới mặc định tạo hierarchy/schema `1.1`: node document → page → leaf, `hierarchy.json`, `hierarchy.sqlite`, collection `routing_nodes`. Leaf evidence/citation vẫn giữ ID nguồn. Snapshot `1.0` chạy câu baseline đơn giản; câu cần research trên snapshot thiếu hierarchy trả `capability_missing`, không rebuild trong query. Nâng cấp tạo snapshot mới, giữ nguồn cũ và không OCR/re-embed leaf:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-upgrade --index .local/indexes/59f1648f8fd3 --output .local/indexes/my_hierarchical --config configs/rag.hierarchical.json
# Snapshot hierarchy hiện có: .local/indexes/hierarchical_20261003
```

Output build/upgrade phải là thư mục mới. Không sửa manifest nguồn để giả capability hoặc xóa lock/data Qdrant khi index đang được mở.

## Luồng code và contracts RAG

Entry point CLI là `__main__ → pipeline.cli.main → pipeline.rag_cli.main`; UI gọi cùng `pipeline.rag_runner.ask`.

```text
ask → config/local policy → index/scope/pending
    → memory rewrite nếu cần → plan_query → resolve_current_scope → analyze_query
    → clarification hoặc route_question (rules, local planner nếu ambiguous)
    → baseline_runner.run_baseline
        → retrieve hybrid → conflict gate → run_visual_phase khi cần
        → evidence assessment → grounded_requirement_answer claims + coverage
    → hoặc research_runner.run_research
        → validated/reused plan → nhiệm vụ đủ dependency
        → retrieve_hierarchical: document/page routing + routed leaf + escape
        → run_visual_phase → extract_fact/validate_fact → coverage/conflict
        → tìm phần thiếu hữu hạn → Decimal calculator hoặc quoted-fact synthesis
    → enforce_verification: đối chiếu mọi ý + kiểm tra câu hỏi đã được giải quyết
    → final trace/citations → một turn memory → đóng tài nguyên
```

Auto có thể escalation baseline → hierarchical tối đa một lần khi thiếu requirement và có hướng tìm tiếp. Conflict, user choice, runtime/config lỗi và hết budget không tạo fallback đổi route để vượt gate. Research không quay lại baseline. Mọi attempt giữ giao scope/filters gốc, kể cả current/revision, escape, parent expansion và crop ảnh.

| Hàm/contract | Input → output | Lỗi, provenance và side effect |
|---|---|---|
| `build_index(run_paths, output, config, registry_path=None)` | Run extraction → snapshot JSON/SQLite/Qdrant/assets | Output mới; kiểm tra IDs/hash/schema; staging trước ready; model local |
| `upgrade_hierarchy(index, output, config)` | Snapshot ready → snapshot mới có nodes/vectors | Giữ leaf vectors/assets; không sửa nguồn; output tồn tại bị từ chối |
| `update_metadata(index, registry, output)` | Metadata có origin/review/source hash → snapshot mới | Không re-embed leaf; source/field sai bị từ chối |
| `suggest_metadata(documents, chunks, registry, config, generator=None, previous=None)` | Text có provenance → artifact gợi ý | Regex + local model có giới hạn; không sửa registry; mọi gợi ý `unreviewed`; reuse chỉ khi nguồn/config/proof còn khớp |
| `suggest_index_metadata(index, output, config)` | Snapshot ready → snapshot mới có `metadata_suggestions.json` | Không OCR/re-embed; registry/vector cũ giữ nguyên; chỉ local model hoặc rules-only |
| `confirm_suggestions(registry, suggestions, documents, chunks, document_id, selections)` | Lựa chọn field → index candidate → registry mới | Kiểm tra lại hash/quote/frame/text; chỉ trường được chọn thành `user_verified`; chưa ghi file |
| `plan_query(question, registry, strategy, filters, search_question=None, visual_policy='auto')` | Query + scope → `QueryPlan` | Metadata verified mới thành hard filter; strategy aliases canonical hóa; unknown revision cần clarification |
| `analyze_query(question, query_plan)` / `route_question(...)` | Query requirements + capabilities + config/budget → analysis/decision | Rules không gọi classifier; ambiguous dùng local validated plan, có thể reuse; không sửa scope |
| `run_baseline(...)` | Plan/analysis + generator/budget → attempt + `EvidenceAssessment` | Validate mọi coverage/source quote; một citation chưa chứng minh đủ requirements |
| `run_research(..., research_plan=None, question_conditions=None, budget=None, progress=None, clarification=None)` | Plan/scope/budget → `AnswerRecord` | T/C/U IDs + DAG + literal condition validation; không tự gán business relation; source thiếu → clarification/abstain |
| `run_visual_phase(root, query_plan, evidence, config, manifest, generator, budget, requirements, task=None)` | Evidence được phép → selected source crops + validated observations | Asset SHA/crop/source do code cấp; tối đa hai ảnh/call; unreadable/ambiguous không tạo giá trị; cache request-local theo task/scope/model |
| `calculate(operations, facts, question_conditions=None, validated_plan=None)` | Typed document facts + validated user conditions → Decimal results | Kiểm tra unit/currency/division; `sources` và `condition_ids` tách biệt; không `eval` hoặc literal tùy ý |
| `ask(index, question, config, strategy=None, filters=None, conversation_id=None, memory_path=None, pending=None, progress=None)` | Request → JSON `AnswerRecord` | Dùng keyword arguments; local policy trước model calls; một turn final; instant không ghi history |
| `enforce_verification(answer, question, generator, config, budget, progress=None)` | Bản nháp + evidence được phép → answer có verification | Mọi dòng có nội dung phải có nguồn; verdict đủ ID, nguồn trích chính xác; thiếu/mâu thuẫn/lỗi/budget → abstain trước memory |

`AnswerRecord` giữ question/answer/status/language, citations/evidence/conflicts, trace/timings/usage. Status gồm `answered`, `abstain`, `clarification`, `conflict`; lỗi config/runtime/schema không bị biến thành đáp án thành công. Text facts giữ literal quote validation. Image facts có `support_kind=image`, `origin=source_visual`, observation/image IDs và `support_text`; caption chưa qua visual phase không là fact. Answering mới tổng hợp text/observations, không tự đính ảnh thêm theo branch baseline.

`AnswerRecord.verification` giữ `state=verified|rejected|error|disabled|not_applicable`, các ý có `claim_id`, verdict `supported|contradicted|insufficient`, quote/source và lý do. Lượt kiểm chứng độc lập với lượt viết đáp án, dùng cùng model local, scope và deadline. Câu nhiều mệnh đề chỉ được supported khi mọi phần đều có căn cứ; `question_supported` kiểm tra yêu cầu còn thiếu. Phép tính replay bằng Decimal và so khớp renderer BT7 trước khi kiểm tra ý nghĩa đầu vào/công thức. Verification lỗi/budget/malformed không được bỏ qua; kết quả chuyển abstain, citation đáp án được xóa, evidence và ledger giữ để debug. Một lần sửa JSON tối đa; chưa tự viết lại đáp án bị bác bỏ hoặc mở rộng retrieval sau verification. Đây là kiểm tra hỗ trợ từ nguồn, không phải xác suất đúng hoặc bảo đảm OCR/nguồn gốc đúng.

`verify_answers=true`, `verification_max_calls=2` mặc định; counter `verification_calls`, token usage và `timings_ms.verification` có trong output. `verify_answers=false` chỉ phục vụ debug/demo so sánh, output ghi rõ disabled. UI có trạng thái và mục kết quả kiểm chứng từng ý. Bộ xác định phạm vi chủ đề tự động chưa được thêm; lựa chọn tài liệu/hard filters hiện có tiếp tục quyết định scope.

## Gợi ý metadata và xác nhận

Build/upload mặc định bật `auto_metadata_suggestions=true`, `metadata_use_llm=true`, tối đa `metadata_max_calls_per_document=2`. Sau chunking/caption, BT4 đọc text nguồn, trích trường có nhãn bằng regex và dùng local LLM đề xuất thêm. Caption ảnh chưa xác minh không làm nguồn metadata. Artifact riêng `metadata_suggestions.json` có candidates theo tài liệu/trường, quote, trang, chunk, source hash, text fingerprint, bbox, origin, cảnh báo và notes về lỗi/giới hạn context. Ngày hiệu lực tự trích yêu cầu ISO `YYYY-MM-DD`; ngày mơ hồ giữ trong notes để nhập tay. Nhiều mã thiết bị/part giữ thành nhiều candidates, không tự chọn một mã.

Gợi ý không đi vào registry hoặc ranking/filter payload. Tab Metadata cho chọn từng candidate và checkbox xác nhận (mặc định bỏ chọn); nút **Xác nhận các gợi ý đã chọn** kiểm tra lại provenance rồi lưu snapshot mới. Chỉ trường đã chọn thành `user_verified`; giá trị đã duyệt không bị tự ghi đè. Các gợi ý còn lại được giữ sau cập nhật registry, còn gợi ý của trường đã sửa/xác nhận bị bỏ. Form/JSON thủ công vẫn là lựa chọn. Registry validation cũ không tự chứng minh ngữ nghĩa của nhãn `source_verified`; extractor mới luôn tạo unreviewed và yêu cầu người dùng xác nhận.

Với snapshot hiện có, dùng nút **Tạo lại gợi ý metadata** hoặc CLI sau; output phải là đường dẫn mới. Không cần chạy lại OCR/embedding:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-suggest-metadata --index .local/indexes/hierarchical_20261003 --output .local/indexes/metadata_drafts --config configs/rag.local.json
# Chỉ regex, không tải/chạy LLM; registry vẫn chờ người dùng xác nhận
& ./.venv/Scripts/python.exe -m denso_a3 rag-suggest-metadata --index .local/indexes/hierarchical_20261003 --output .local/indexes/metadata_rules --rules-only --config configs/rag.local.json
& ./.venv/Scripts/python.exe evaluation/demo_verification_metadata.py --output results/my_verification_metadata_demo
& ./.venv/Scripts/python.exe evaluation/demo_verification_metadata.py --production --output results/my_verification_source_demo
```

Upload gộp corpus sẽ reuse gợi ý còn khớp từ snapshot cung cấp `--registry`; thay đổi nguồn/text/metadata/config/model làm cache invalid. Không tự download model hoặc chuyển cloud; local extractor lỗi vẫn giữ gợi ý regex và ghi notes, không biến draft thành dữ liệu đã duyệt.

Kiểm chứng hai tính năng ngày 04/10/2026: 208 regression tests pass, demo synthetic có model local thật 11/11 checks và hai smoke checks tài liệu thật đạt. Demo before/after chủ động đưa bản nháp sai vào lượt generation để tái hiện lỗi ổn định; verifier chạy model thật. Đây không phải tỷ lệ đúng toàn corpus. [Báo cáo](docs/3-10/ANSWER_VERIFICATION_METADATA_REPORT.md) ghi rõ regimes, lỗi đã phát hiện/sửa và kết quả cuối.

Citation bbox `[x0,y0,x1,y1]` chuẩn hóa theo ảnh trang canonical, gốc trái trên. PDF page bắt đầu từ 1; ảnh độc lập có page `null`. Visual crop citation giữ bbox trong frame nguồn; observation validation xác lập provenance/format, không chứng minh model đọc pixel đúng.

Một `QueryBudget` dùng monotonic clock xuyên rewrite/router/retrieval/generation/escalation: mặc định 300 giây, 12 logical retrieval calls, 1 escalation, 2 router attempts gồm repair, 6 visual calls gồm repair và 2 ảnh/call. Research giữ 6 tasks/3 rounds, stage deadline không vượt thời gian còn lại; một task retrieval bao gồm routed + escape nên logical calls khác số backend searches. Context/calls/deadline không được reset khi escalation. Trace ghi initial/final route, method/reason/capability, attempts, missing coverage, visual sources, counters, tokens và timings. Model reload tuần tự; latency gồm load, không phải steady-state inference.

## Điều kiện trong câu hỏi, memory và migration

Kênh `--assumptions`, `ask(..., assumptions=...)` và config `research_assumptions` đã bỏ. CLI/config/fixture cũ báo migration rõ. `configs/bat.assumptions.json` chỉ được giữ với nhãn lịch sử; sản phẩm không đọc nó. Với BAT, “hoàn thành 100% mục tiêu giảm” không tự có nghĩa “100% lượng giảm thành doanh số BAT”. Thiếu quan hệ này phải tìm evidence hoặc hỏi làm rõ.

Điều kiện nói rõ trong question/clarification có U ID riêng, raw literal, `start/end`, `origin=user_question|user_clarification`, `applies_to` và value/unit khi parse được. Span phải khớp chuỗi nguồn; điều kiện numeric unsupported/ambiguous bị từ chối. Phần trăm explicit có trace chia 100; không tự dùng mọi “100%” làm cùng business ratio. Renderer ghi điều kiện người dùng riêng, không tạo document citation giả.

Instant pending ở Streamlit RAM; long-term `turns`/`pending_research` ở `.local/conversations.sqlite`. Pending `schema_version=2.0` có scope/content/config fingerprints và câu gốc/câu làm rõ; không tin lại facts/assumptions cũ. Đổi index/content/filter/config invalidates pending; legacy pending cần đặt lại câu hỏi, history vẫn được giữ. Conversation chỉ giải quyết tham chiếu, không là source evidence. UI result cache cũng theo corpus/scope/config/memory.

```powershell
# Fixture mới tách thiếu transfer và transfer được nói rõ ngay trong question
$demo = Get-Content evaluation/fixtures/bat_scenario.json -Raw -Encoding utf8 | ConvertFrom-Json
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question $demo.questions[0].question --config configs/rag.local.json --output results/my_demo/bat_no_transfer.json
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question $demo.questions[1].question --config configs/rag.local.json --output results/my_demo/bat_explicit_transfer.json
```

## Debug, kiểm thử và benchmark

### Một file đánh giá để làm slide (20–30 câu)

Điền câu hỏi trong [`evaluation/slide_questions.json`](evaluation/slide_questions.json) (25 dòng mẫu; dòng trống được bỏ qua), rồi chạy `evaluation/run_slide_test.ps1 -Index "<snapshot ready đang dùng>"`. Nếu cần, truyền `-Config` đúng file cấu hình đang dùng trong UI. Hoặc gọi Python trực tiếp:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate-slides --index ".local/indexes/TEN_SNAPSHOT"
```

`TEN_SNAPSHOT` là placeholder. Có thể bỏ `--index` khi `.local/workspace.json` đã lưu đúng index còn tồn tại. Output mặc định là **một HTML độc lập** tại `results/slide_evaluations/evaluation_<timestamp>_<id>.html`, có số liệu tổng hợp, chi tiết từng câu và toàn bộ JSON nhúng. File được cập nhật atomic sau từng câu, giữ cả lỗi; không ghi đè báo cáo cũ. `--limit 1` chạy smoke check; bỏ limit khi chạy đủ bộ. Chi tiết: [hướng dẫn đánh giá cho slide](docs/SLIDE_EVALUATION_GUIDE.md).

Luồng: `rag_cli.main → slide_report.evaluate_for_slides → rag_runner.ask` tuần tự từng câu độc lập, rồi `summarize → render_report`. Input gồm JSON `questions` (`id`, `question`, optional `category`, `filters`, `expected_answer`, `reference`, `expected_status`), index ready và config local. Đáp án tham khảo không vào inference. Output lưu status/answer/citations/evidence/verification/trace/timing, config/model/môi trường và code/index fingerprints; citation giữ nguyên tọa độ nguồn. `read_report(path)` đọc lại JSON đầy đủ từ HTML mà không chạy model.

Số liệu gồm số câu, tỷ lệ trả lời/lỗi, có citation/qua verifier, khớp trạng thái kỳ vọng, latency trung bình/P50/P95 và token có số đo. **Độ đúng nội dung chưa được chấm tự động**; không gọi tỷ lệ trả lời hoặc verified là accuracy. Latency tách câu có đáp án, câu không trả lời và lỗi, có tính tải model. Không ghi history hoặc sửa index. Lỗi từng câu được lưu và chạy tiếp; Ctrl+C khi inference lưu báo cáo chưa hoàn tất (exit 130), có lỗi runtime trả exit 1. Lỗi thiết lập được báo trước khi inference. Snapshot và ảnh gốc phải giữ riêng để đối chiếu; `results/` bị gitignore nên cần sao lưu file HTML riêng.

Forced `--mode baseline|hierarchical` và `--strategy lexical|dense|hybrid` phục vụ debug/A-B; production dùng auto. Alias strategy `auto` là hybrid + visual auto; `visual` là hybrid + visual required, không phải image embedding hay kiến trúc thứ ba. `rag.baseline.json`/`rag.flat.json` giữ forced baseline; `rag.hierarchical.json` giữ forced hierarchical. API adapter cũ vẫn tồn tại để tương thích code; UI/product local chặn API và auto không cloud fallback.

```powershell
& ./.venv/Scripts/python.exe -m pytest tests/test_ui_auto.py tests/test_rag_interfaces.py -q
& ./.venv/Scripts/python.exe -m pytest -q
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate --index results/rag_main --qa evaluation/data/dataset_test.json --output results/eval_auto --config configs/rag.local.json
# Cùng index/gold/filters/model/budget; chỉ thay mode và thư mục output mới
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate-multidoc --index .local/indexes/hierarchical_20261003 --qa results/reviewed_gold.json --output results/abc_baseline --config configs/rag.local.json --mode baseline
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate-multidoc --index .local/indexes/hierarchical_20261003 --qa results/reviewed_gold.json --output results/abc_hierarchical --config configs/rag.local.json --mode hierarchical
& ./.venv/Scripts/python.exe -m denso_a3 rag-evaluate-multidoc --index .local/indexes/hierarchical_20261003 --qa results/reviewed_gold.json --output results/abc_auto --config configs/rag.local.json --mode auto
```

Multi-document gold phải có `review_status=human_reviewed`, `questions` với expected status, evidence alternatives/calculations và optional `acceptable_routes`; nhãn gold không đi vào inference. Evaluator giữ mode được truyền, ghi records/summary, P50/P95 total/stages, routing agreement, escalation/counters/tokens và budget exits. Single-source draft labels/substring/Hit@5/citation pointer chỉ là proxy; correctness ngữ nghĩa và observation ảnh cần review nguồn. Fixture draft không tự trở thành reviewed gold.

AppTest dùng scripted answers xác nhận widget/pending/scope/citation UI; không chứng minh năng lực model. Báo cáo implementation ghi riêng regression, demo model local thật và giới hạn benchmark. Chưa có A/B/C human-reviewed toàn corpus để kết luận kiến trúc nào nhanh/chính xác hơn; coverage/quote/pointer hợp lệ không phải confidence 99%.

Kiểm chứng cập nhật 03–04/10/2026: 173 tests pass; sáu ca model local cùng snapshot cho A/B/C (auto và hierarchical 6/6, forced baseline 4/6 vì từ chối hai phép tính). Có thêm ba ca tài liệu thật và một ca dùng nguyên config production có reranker. Kết quả, lỗi ban đầu và cách sửa nằm trong [báo cáo](docs/3-10/rag_architecture/UPDATE_IMPLEMENTATION_REPORT.md). Tái hiện demo bằng output mới:

```powershell
& ./.venv/Scripts/python.exe evaluation/demo_auto_update.py --output results/my_auto_demo
& ./.venv/Scripts/python.exe evaluation/demo_auto_update.py --production --modes auto --output results/my_source_demo
```

## Pipeline extraction hiện có

Đây là dự án chính cho pipeline ingestion/extraction BT1–BT3. Entry point là `python -m denso_a3`, đọc mặc định toàn bộ `evaluation/data` và ghi kết quả vào `results/`. Mã trước đây nằm trong `denso_demo/` đã được chuyển lên gốc. `AGENTS.md`, `evaluation/` và tài liệu trong `docs/` được giữ và gộp tại gốc. Output Tesseract cũ trong `evaluation/results/` đã được dọn vì pipeline hiện tại không sử dụng; [README baseline cũ](docs/codeflow/BT2_BASELINE_README.md) vẫn được lưu để tham khảo lịch sử.

Kiến trúc C dùng **Docling StandardPdfPipeline** với **RapidOCR Torch + layout Heron + TableFormer accurate**. Cả ba model phải nằm trên CUDA khi chọn `--device cuda` (mặc định); nếu không xác nhận được device, pipeline báo lỗi. Decode/render ảnh, hậu xử lý và tính metric chạy trên CPU. Không tự chuyển OCR sang CPU.

## Môi trường

Yêu cầu Python **3.12**, NVIDIA driver hỗ trợ CUDA 12.8 và dung lượng trống cho venv/model/results. Trên máy này, hãy gọi trực tiếp Python 3.12 trong `.venv` thay vì giả định lệnh `python` mặc định trỏ đến đúng phiên bản.

Tạo mới trên máy có Python 3.12:

```powershell
py -3.12 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt -c cuda-constraints.txt
& ./.venv/Scripts/python.exe -m pip check
```

`requirements.lock.txt` ghi phiên bản toàn bộ dependency từ môi trường đã kiểm chứng. Cài CUDA trước rồi có thể cài lock thay `requirements.txt`; lock có phiên bản `+cu128` cần đúng nguồn wheel PyTorch. Dự án dùng `docling-slim` với các extra PDF/model/OCR cần thiết; package này cung cấp chính API `docling`, tránh cài các reader Office không dùng trong dataset.

Bootstrap model cần mạng, chỉ thực hiện một lần hoặc khi đổi model/cache:

```powershell
& ./.venv/Scripts/python.exe -m denso_a3 doctor --download-models
& ./.venv/Scripts/python.exe -m denso_a3 doctor
```

Lệnh thứ hai chạy offline và kiểm tra **device thực tế của weights**, không chỉ cờ cấu hình. Model/cache ở `.cache/models`; RapidOCR checkpoints và dictionary được giữ tại đây. Hugging Face tải tuần tự đúng snapshot layout và TableFormer accurate; không cần token cho các model public.

## Chạy từ thư mục gốc project

```powershell
# Chỉ BT1; không cần khởi tạo GPU/model
& ./.venv/Scripts/python.exe -m denso_a3 ingest

# Một run mới cho toàn bộ data
$fullRun = "results/full_$(Get-Date -Format yyyyMMdd_HHmmss)"
& ./.venv/Scripts/python.exe -m denso_a3 run --output $fullRun

# Pilot / benchmark: đúng tên source tương đối và số trang vật lý từ 1
$pilotRun = "results/pilot_$(Get-Date -Format yyyyMMdd_HHmmss)"
& ./.venv/Scripts/python.exe -m denso_a3 run --source nhwc0428.pdf --pages 2 8 11 13 15 --output $pilotRun

# Đánh giá lại sau khi sửa ground truth; không chạy lại OCR
& ./.venv/Scripts/python.exe -m denso_a3 evaluate --run $fullRun

# Test thực tế BT1, contract, evaluation và timeout worker; không tải model
& ./.venv/Scripts/python.exe -m pytest tests/test_pipeline.py -q
```

Không ghi đè thư mục run đã có nội dung. `--input`, `--dpi`, `--threads`, `--timeout`, `--startup-timeout`, `--limit`, `--source`, `--pages` dùng để chạy tập con/benchmark. `--bypass-cache` bỏ cache disk nhưng vẫn dùng một lần inference cho các alias trong cùng run. Cache cũ không được coi là latency inference mới.

## Luồng và contract

```text
CLI main
  → PipelineConfig.validate
  → runner.run
      → bt1.ingest → documents + PageInput[] + ingestion summary
      → InferenceWorker.start → DoclingBackend + runtime/device/model hashes
      → cache lookup hoặc worker.convert(canonical PNG)
          → Docling convert: OCR → layout → TableFormer → document assembly
          → normalize_payload → BT2 raw lines + BT3 blocks/grid
      → bind_payload: gắn ID riêng cho từng nguồn/alias
      → atomic artifacts + HTML/Markdown bảng
      → continuation_candidates (chỉ đề xuất, không tự nối bảng)
      → evaluation.evaluate (lúc này mới đọc .ocr/pilot)
      → create_review → review.html + ảnh nguồn
```

| Hàm chính | Input → Output | Side effect / lỗi |
|---|---|---|
| `ingest(config)` | Thư mục PDF/ảnh → documents, `PageInput[]`, summary | Canonical PNG/cache; lỗi từng file/trang; sidecar không làm input inference |
| `DoclingBackend.convert(image_path)` | Một PNG → BT2/BT3 + raw Docling + timing | Một conversion joint; GPU inference; lỗi model/runtime không chuyển backend |
| `normalize_payload(raw, cells, frame, order, status, errors)` | Native bbox/text/grid → JSON chuẩn | Không tạo bbox word giả; lỗi geometry giữ raw text và đánh dấu partial |
| `bind_payload(payload, page)` | Payload cache theo nội dung → payload theo nguồn | Copy payload; ID spans/blocks/tables độc lập giữa alias |
| `run(config, run_dir, ...)` | Dataset/config → performance summary | Chạy worker, ghi artifacts, đánh giá và review; output mới bắt buộc |
| `evaluate(run_dir, data_root, pilot)` | Predictions đã lưu + sidecar độc lập → metrics | Chỉ ghi metric/registry; không sửa prediction |

`PageInput` giữ `page_id`, `document_id`, đường dẫn/hash nguồn, số trang, loại trang, PNG/hash/dimensions, status, metadata. ID theo **nguồn tương đối**; alias dùng hash ảnh canonical để tiết kiệm inference nhưng không mất record.

PDF luôn render trang hiển thị rồi OCR ảnh. Với PDF vừa thêm, text layer ẩn do OCRmyPDF/Tesseract tạo ra được ghi nhận trong metadata nhưng không đưa vào recognizer. Với PDF native trong tương lai, pipeline này vẫn force fresh OCR để giữ chính sách C đã chọn, không dùng text layer như một nhánh tối ưu.

Geometry output: `[x0,y0,x1,y1]`, gốc trái trên, chuẩn hóa `[0,1]` theo **canonical page image**. PDF có matrix `pdf_points_to_canonical` và inverse; tọa độ PDF tính trong frame PyMuPDF của trang đã crop, trước rotation. Ảnh giữ EXIF transform từ pixel nguồn. Bbox native và source frame được giữ để audit. OCR span có độ chính xác **line**, không phải word. Bbox cell là text-region do engine trả về, không nhất thiết là toàn bộ ô bảng.

BT2 text lấy trực tiếp chuỗi OCR cells, chưa áp dụng reading order BT3. BT3 lưu block type/order và origin của thứ tự (Docling assembly hoặc item chưa liệt kê được append). Bảng có `num_rows`, `num_columns`, mỗi cell có row/column từ 0, rowspan/colspan và `text_raw`. Vị trí grid thiếu nằm trong `unknown_grid_positions`, không tạo cell/text/bbox giả; bảng và BT3 báo `partial` khi grid chưa đầy đủ. Ô không có chữ được đánh dấu chưa xác nhận trống; demo không dựng lại tên bị che đen. HTML giữ merged cells; Markdown chỉ là view, có thể mất ý nghĩa merge.

Các trạng thái `ok`, `partial`, `failed` nói về thực thi/schema; **`ok` không chứng minh OCR đúng**. Quality vẫn `unreviewed`. Timeout parent dừng worker, ghi lỗi trang và tạo worker mới cho trang tiếp theo. Startup lỗi làm run thất bại; cache không được dùng để che việc model/device chưa hợp lệ.

## Đọc kết quả để kiểm tra OCR

Mở `results/<run>/review.html` trong trình duyệt. Chọn trang rồi bật/tắt OCR line boxes, layout, ô bảng; đối chiếu ảnh với text và bảng HTML. Tab reference/diff giúp phát hiện chữ thiếu/thừa nhưng chính ảnh gốc mới quyết định bản nào đúng. Ghi chú review có thể xuất JSON; chúng chưa tự trở thành ground truth.

| Artifact | Nội dung |
|---|---|
| `documents.json`, `pages.json`, `ingestion.json` | Inventory, route, alias, hash, geometry, lỗi BT1 |
| `bt2.json`, `bt3.json` | Tất cả logical pages; text/spans và layout/grid tách riêng |
| `pages/<id>/ocr.txt`, `bt2.json`, `bt3.json`, `table_N.html/.md` | Output từng trang |
| `raw/<image_hash>.json` | Native Docling document + raw OCR cells; lưu một lần cho ảnh trùng |
| `runtime.json`, `execution.json`, `performance.json` | Phiên bản, hash model/code, device, cold start, inference, cache, peak CUDA memory |
| `ground_truth_registry.json`, `metrics.json` | Nguồn reference, căn chỉnh trang, CER/WER, BT3 pilot |
| `continuation_candidates.json` | Khả năng bảng nối trang; luôn cần review |
| `review.html`, `assets/*.png` | Review offline không phụ thuộc đường cache gốc |

Cache key gồm hash canonical image, inference config (DPI/backend/device/lang/table mode), phiên bản engine/renderer, hash model và code/schema. Không gồm ground truth hoặc QA answers. Chỉ payload conversion thành công và không có lỗi được cache lâu dài; alias reuse trong run vẫn giữ status nguyên gốc.

Các run cũ đã được chuyển vào `results/` và vẫn chứa snapshot đường dẫn tuyệt đối từ thời điểm chạy. `review.html` dùng ảnh đã đóng gói trong từng run; lệnh `evaluate` có thể tái dùng các ảnh này khi tạo lại review. Vì mã đã đổi khi chuyển lên gốc, khóa prediction cache cũ không còn khớp; lần chạy `run` mới sẽ suy luận lại, còn model weights và ảnh render trong `.cache/` vẫn dùng được.

## Diễn giải đánh giá

`.ocr` của PDF được tách bởi form-feed, căn đúng số trang tài liệu. Mismatch làm reference `unaligned`, không đo điểm sai trang. CER/WER ghi raw và whitespace-normalized, micro/macro, số S/D/I; CER có thể vượt 100% khi prediction thừa. Reference rỗng có denominator 0 → rate `null`, không ép thành 0. Prediction thất bại vẫn bị tính như text thiếu khi reference hợp lệ.

PDF có supplied reference **chưa được soát**, nên metric là *agreement*, chưa phải độ chính xác có chứng nhận. 50 ảnh hiện chưa có transcript: CER/WER là **N/A**; đáp án QA không được dùng thay transcript.

`ground_truth/pilot.json`: nhãn nhìn từ nguồn, origin `agent_visual_source_review`, status `needs_user_review`. Pilot có table detection exhaustive trên 5 trang; layout chỉ gắn một phần; full grid/text chỉ một bảng. Matching one-to-one tại IoU ≥ 0.5. Precision/F1 table chỉ hợp lệ trong phạm vi exhaustive; layout chỉ báo recall trên vùng đã gắn nhãn, mAP là N/A. Cell exact match bỏ qua whitespace và không bỏ dấu/chữ hoa. Không báo GriTS toàn bộ dataset khi thiếu nhãn topology đầy đủ.

Để kết luận “đọc đúng”, hãy soát pilot/GT từ ảnh, bổ sung transcript cho ảnh, gắn nhãn bảng ở tập hold-out và chốt ngưỡng chấp nhận theo dữ liệu thực tế. Confidence hoặc một run không lỗi không thay cho các bước này. Chi tiết luồng và số đo thực tế nằm trong [báo cáo triển khai](docs/codeflow/IMPLEMENTATION_REPORT.md).

## Cấu trúc

```text
denso_a3/
  __main__.py  # python -m denso_a3
  contracts.py, io.py
  bt1/ingestion.py
  bt2/quality.py
  bt3/tables.py
  backends/docling_backend.py, normalization.py
  pipeline/cli.py, runner.py, worker.py
  evaluation/text.py, structural.py, evaluator.py
  review/html.py, template.html
evaluation/data/  # PDF, ảnh và nhãn QA gốc; không phải package đánh giá của pipeline
ground_truth/pilot.json, create_pilot.py
tests/test_pipeline.py
docs/  # kế hoạch BT1–BT3, BT2, RAG và báo cáo triển khai
AGENTS.md, .cursor/rules/project-documentation.mdc
requirements.txt, requirements.lock.txt, cuda-constraints.txt
.venv/, .cache/, results/  # local generated, gitignored
```

Đọc API hiện tại từ đúng wheel/phiên bản đã cài là cơ sở của adapter. Tham khảo nền tảng: [Docling standard pipeline](https://docling-project.github.io/docling/), [Docling GPU](https://docling-project.github.io/docling/usage/gpu/), [PyTorch CUDA 12.8 version pair](https://pytorch.org/get-started/previous-versions/).

</details>
