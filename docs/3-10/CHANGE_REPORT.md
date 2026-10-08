# Báo cáo triển khai hierarchical retrieval và tìm kiếm nhiều bước

Ngày: 03/10/2026. Workspace: `D:\Denso\project`.

## 1. Kết quả

Đã triển khai đường xử lý `hierarchical`: **tìm tài liệu → tìm trang → tìm đoạn/bảng**, chia câu hỏi thành nhiệm vụ, thu thập dữ kiện có nguồn, rồi tìm tiếp từ dữ kiện trung gian hoặc phần còn thiếu. Có bộ tính Decimal cho câu hỏi tính toán và giữ đường `baseline` để so sánh.

Câu BAT đã chạy thật với BGE-M3, BGE reranker và Qwen3-VL-2B local trên GPU RTX 4050 Laptop 6 GB. Hệ thống tìm đủ 5 dữ kiện tại trang 5, 10, 11 của `nhwc0428.pdf`, trả `answered`:

| Đại lượng | Kết quả |
|---|---:|
| Sản lượng mục tiêu Jessie + Dawn | 1.220.000.000 sticks |
| Gross Margin | 10.492.000 GBP |
| Tổng Latest Estimate | 1.572.000 GBP |
| Gross Margin trừ Latest Estimate | 8.920.000 GBP |

Giả định **100% sản lượng thuốc lá bất hợp pháp bị cắt giảm chuyển thành doanh số BAT** được lưu và hiển thị là giả định người dùng. “Lợi nhuận ròng” trong demo chỉ là Gross Margin trừ hai ngân sách, chưa tính thuế hoặc những chi phí khác. Số đầu vào xuất phát từ OCR chưa được người kiểm duyệt; phép tính đúng không chứng minh OCR đúng.

## 2. Các file mới

Đường dẫn code trong bảng tính từ thư mục gốc project.

| File | Nội dung và mục đích |
|---|---|
| `denso_a3/bt4/hierarchy.py` | Xây node tài liệu và trang; liên kết leaf chunk; synopsis trích nguyên văn để định tuyến; kiểm tra graph và nguồn |
| `denso_a3/bt5/hierarchy_store.py` | SQLite FTS cho node và collection Qdrant `routing_nodes`; dùng chung Qdrant client với store leaf |
| `denso_a3/bt6/task_planner.py` | Planner LLM có JSON schema; kiểm tra số nhiệm vụ, dependency, query trung gian và DAG phép tính |
| `denso_a3/bt6/hierarchical_retrieval.py` | Truy xuất theo tầng, thêm nhánh tìm leaf toàn phạm vi đã lọc và mở rộng table leaf hợp lệ |
| `denso_a3/bt6/evidence_coverage.py` | Ledger supported/missing; kiểm tra bất đồng của các candidate số đã đối chiếu |
| `denso_a3/bt7/fact_extraction.py` | Trích fact theo nhiệm vụ; kiểm tra quote/literal/thực thể/đơn vị/hàng-cột; quy tắc đọc số trực tiếp và LLM fallback |
| `denso_a3/bt7/calculation.py` | Chuẩn hóa số, scale, currency, per mille; bốn phép toán Decimal, kiểm tra dimension và provenance |
| `denso_a3/pipeline/research_runner.py` | Điều phối các vòng tìm → trích fact → coverage → tìm tiếp → tính/tổng hợp; không chứa SQL hoặc adapter model |
| `denso_a3/evaluation/multidoc_evaluator.py` | Đánh giá bộ bằng chứng và kết quả tính trên gold được người kiểm duyệt |
| `configs/rag.hierarchical.json` | Cấu hình local/CUDA, chế độ mới và giới hạn tài nguyên |
| `configs/bat.assumptions.json` | Giả định BAT đã được người dùng chốt |
| `evaluation/fixtures/bat_scenario.json` | Câu hỏi demo và giả định; nhãn `draft_from_existing_ocr`, không phải gold được kiểm duyệt |
| `tests/test_hierarchical.py` | Kiểm thử graph, scope, planner, source facts, phép tính, multi-file, bridge, mâu thuẫn, memory và budgets |

## 3. Các file được sửa

| File | Thay đổi |
|---|---|
| `denso_a3/contracts.py` | Contract `HierarchyNode`, `ResearchTask`, `EvidenceFact`; schema/capability; research config và lỗi context budget |
| `denso_a3/pipeline/index_builder.py` | Build mới tạo hierarchy; thêm `upgrade_hierarchy` tạo snapshot mới từ index cũ, giữ vector leaf và không OCR lại |
| `denso_a3/pipeline/rag_runner.py` | `ask` dispatch baseline/hierarchical; nhận assumptions, pending clarification và progress callback; kiểm tra capability/scope |
| `denso_a3/pipeline/rag_cli.py` | `rag-upgrade`, `rag-evaluate-multidoc`, `rag-ask --mode/--assumptions`; benchmark có lỗi trả exit code khác 0 |
| `denso_a3/backends/generation.py` | Có thể đóng/mở lại local server giữa các phase; đếm token bằng llama.cpp; deadline cho startup/generation |
| `denso_a3/bt6/retrieval.py` | Nhận query vector có sẵn để dùng chung cho routing và hai nhánh leaf |
| `denso_a3/bt6/planner.py` | Không hiểu nhầm cột `Latest Estimate` thành yêu cầu revision hiện hành |
| `denso_a3/bt7/answering.py` | Guard token cho research; tổng hợp claims có source IDs, code render citation từng ý và kiểm tra đủ source; giữ API baseline |
| `denso_a3/bt7/memory.py` | Lưu pending research; lọc history theo scope trước LIMIT |
| `denso_a3/ui/app.py` | Chọn mode; tạo snapshot phân tầng; nhập assumptions; tiến độ, coverage, stop reason; pending trong session |
| `README.md` | Luồng build/hỏi mới, API, cấu hình, migration, side effects và giới hạn |

## 4. Những kiểm tra quan trọng đã thêm

- Synopsis chỉ dùng chọn nguồn; fact phải quay về chunk gốc. Visual caption chưa xác minh không được dùng làm toán hạng.
- Quote phải là đoạn liên tục có thật trong source text; raw value phải nằm trong quote. Quote cần xác định đúng thực thể được hỏi.
- Latest Estimate cần khớp cả **hàng của dự án** và **cột Latest Estimate** trong table cells quan sát được. Không dùng cột Co plan/Variance hoặc cộng cả GBP và USD của cùng ô.
- Những dạng target volume, gross-margin rate và Latest Estimate rõ ràng được đọc bằng quy tắc từ source. Quy tắc không chứa tên Jessie/Dawn, filename hay các đáp số BAT. Dạng khác dùng LLM và cùng validator.
- Dependency chỉ trỏ tới nhiệm vụ trước đó; query `{T1}` được thay bằng fact của T1 có nguồn. Không dùng answer lịch sử làm bridge evidence.
- Entity `{T1}` cũng được thay bằng fact nguồn, không tìm theo nhãn “nhà sản xuất” hoặc đại từ “công ty đó”. Validator cấm dùng fact text làm toán hạng. Ví dụ định dạng planner được chọn theo loại câu hỏi, không ép câu đơn giản theo recipe tài chính.
- Calculator nhận ID fact/result, không nhận biểu thức code và không dùng `eval`; giữ danh sách nguồn cho từng kết quả.
- Hard filters giữ nguyên qua routing, escape và parent expansion. Parent chứa phần bị loại dùng synopsis từ những leaf còn được phép, không dùng vector synopsis trộn.
- Dừng khi đủ coverage, hết vòng/calls/deadline hoặc không còn query hữu ích. Thiếu đầu vào thì `abstain`, không mặc định chi phí bằng 0.

## 5. Artifact đã tạo trên máy

| Đường dẫn | Trạng thái |
|---|---|
| `.local/indexes/hierarchical_20261003/` | Snapshot ready mới, schema 1.1/hierarchy 1.0; 1.217 leaf chunks, 173 node = 53 tài liệu + 120 trang |
| `.local/indexes/59f1648f8fd3/` | Snapshot cũ vẫn tồn tại; không bị migration ghi đè |
| `.local/workspace.json` | Vẫn trỏ tới snapshot cũ trong lần kiểm chứng; demo có bước chọn snapshot mới |
| `results/hierarchical_demo/bat_answer.json` | Kết quả chạy thật qua API Python `ask`, khoảng 85,48 giây; 1 vòng, 5 retrieval calls, 4 citation |
| `results/hierarchical_demo/streamlit_bat_answer.json` | Kết quả gửi câu qua Streamlit AppTest với model thật, khoảng 91,86 giây; 0 exception/0 error UI |
| `results/hierarchical_demo/bridge_local_answer.json` | Bridge fixture 2 nguồn với planner/extractor/synthesis local thật; 3 vòng, khoảng 30,01 giây, citation từng ý |
| `results/hierarchical_demo/bridge_controlled_index/` | Fixture 2 ảnh nguồn có text cung cấp sẵn, lexical routing, không OCR/embedding/reranker; vector giả không được dùng khi query |

Các JSON là file được script kiểm chứng chủ động xuất. UI ở chế độ tức thì không tự tạo chúng. `build_seconds` trong manifest nâng cấp được kế thừa từ index cũ, không phải thời gian upgrade đo mới. `.local/`, `.cache/`, `results/` bị Git ignore, cần giữ/copy riêng nếu chuyển demo sang máy khác.

## 6. Kiểm chứng và mức độ kết luận

Chạy regression bằng:

```powershell
& ./.venv/Scripts/python.exe -m pytest tests/test_hierarchical.py tests/test_rag.py tests/test_pipeline.py -q
```

Kiểm tra cuối: **51 tests passed**; `compileall` thành công; CLI help của hai lệnh mới hoạt động; graph snapshot và các liên kết trong 4 báo cáo hợp lệ. Chạy lại source rules cuối trên evidence của lần BAT đã lưu vẫn lấy đúng 5 input và cho kết quả 8.920.000 GBP.

Bộ regression kiểm tra code thật của graph/SQLite/Qdrant local/validator/calculator; model trong các ca integration tổng hợp được thay bằng scripted generator. Ca bridge dùng A chứa `Orion → Vega`, B chứa `Vega → cost`, và kiểm tra query vòng sau thực sự dùng Vega. Ca 5 file kiểm tra đủ các input, phép tính và citation riêng; ca mâu thuẫn kiểm tra không chọn đại một ngân sách.

Lần chạy BAT thật và lần gửi qua Streamlit dùng model local thật; câu BAT trong corpus hiện có là **nhiều trang của một PDF**, không phải chứng minh model thật trên nhiều PDF độc lập. Bridge 2 nguồn đã chạy planner/extractor/synthesis thật trên fixture lexical có text cung cấp sẵn: vòng 1 lấy Vega, vòng 2 tìm `Vega cost` nhưng model báo chưa tìm được fact, vòng 3 query bổ sung `cost` rồi xác minh được GBP100. Đây là kiểm tra vòng tìm bổ sung, không là benchmark OCR/dense retrieval hay nhiều PDF thực tế. Chưa có benchmark A/B toàn corpus với human-reviewed gold, P50/P95 hoặc peak VRAM của query.

## 7. Phạm vi so với kế hoạch ban đầu

| Phần của kế hoạch | Hiện trạng |
|---|---|
| Hierarchy, routing nhiều tài liệu/trang, escape leaf | Đã triển khai; tầng giữa dùng trang vật lý, chưa gom mục xuyên trang |
| Planner, dependency, bridge search, query bổ sung, coverage, budgets | Đã triển khai; phụ thuộc chất lượng planner và quote extraction local |
| Facts có nguồn, giả định BAT, Decimal arithmetic, citation | Đã triển khai; parser hỗ trợ tập dạng số/đơn vị hữu hạn |
| UI/CLI/migration/memory/pending | Đã triển khai; trả lời clarification làm lại plan và lấy lại evidence trong scope hiện tại |
| Mâu thuẫn | Gate kỹ thuật cũ + so sánh candidate số của các source rule; chưa kiểm tra mọi bất đồng nghiệp vụ/văn bản |
| OCR khó đọc → xác nhận bằng VLM cho research fact | Chưa triển khai; research từ chối caption-only, baseline VLM vẫn hoạt động |
| Reuse weights cho toàn batch nhiệm vụ | Chưa tối ưu; query vector reuse trong một nhiệm vụ, weights vẫn reload nhiều lần |
| Gold được người soát, A/B/ablation và rollout mặc định | Chưa hoàn tất; không tự gán nhãn human-reviewed hoặc tuyên bố vượt baseline |

Không có ngưỡng “confidence 99%” cho câu trả lời. Coverage là điều kiện về các nhiệm vụ **được planner nhận ra**, không bảo đảm planner không bỏ sót dữ kiện hay tất cả nguồn phản bác trong kho đều đã được đọc.

Xem [kiến trúc](SYSTEM_ARCHITECTURE.md), [demo và luồng code](DEMO_CODE_FLOW.md), [so sánh](ARCHITECTURE_COMPARISON.md).
