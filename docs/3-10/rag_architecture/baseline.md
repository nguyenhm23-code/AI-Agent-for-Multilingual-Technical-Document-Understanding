# Baseline trong hệ thống tự định tuyến

Ngày đối chiếu code: 03/10/2026. Đây là mô tả implementation hiện tại; kết quả chạy cụ thể nằm trong [báo cáo](UPDATE_IMPLEMENTATION_REPORT.md). Các số đo/demo trước cập nhật được giữ trong [CHANGE_REPORT](../CHANGE_REPORT.md) và không tự chứng minh chất lượng auto mới.

Baseline là runner tra cứu trực tiếp leaf chunks trong scope được phép. Hybrid là retrieval keyword + semantic; auto là dispatcher chọn baseline/hierarchical, không phải một backend tìm kiếm thứ ba. UI không chọn runner hoặc strategy. Forced `--mode baseline` phục vụ debug/A-B.

## Chuẩn bị nguồn

BT1–BT3 tạo OCR/layout/table với nguồn và geometry; `build_chunks` tạo text/table/figure/page_visual. Index lưu records/FTS5 trong SQLite, vectors trong Qdrant, registry metadata, parents, source assets và manifest ready. Build có thể thêm document/page hierarchy nhưng baseline không rank hierarchy để chọn vùng tìm.

Chunk giữ chunk/document/page IDs, source/image SHA, text/table, block/span IDs, bbox và quality flags. PDF page từ 1, ảnh riêng có page `null`. Bbox `[x0,y0,x1,y1]` chuẩn hóa theo ảnh trang canonical, gốc trái trên. Query không chạy lại OCR, không thêm vector câu hỏi vào index và không rebuild snapshot.

## Dispatcher trước runner

`rag_runner.ask(index, question, config, strategy=None, filters=None, conversation_id=None, memory_path=None, pending=None, progress=None)` được gọi bằng keyword arguments từ UI/CLI/evaluator.

```text
config.validate/local policy → index ready/schema → scope/content/config fingerprints
→ resolve pending hoặc memory rewrite → plan_query → resolve_current_scope
→ analyze_query → route_question → baseline_runner.run_baseline
```

Local policy được kiểm tra trước rewrite/router/planner. Memory chỉ giải quyết tham chiếu; answer cũ không thành evidence. Pending version 2.0 giữ câu gốc/câu làm rõ cùng fingerprints; legacy hoặc content/scope/config khác bị invalidated, history vẫn xem được.

`plan_query` tạo `QueryPlan` có question/search_question/language, canonical strategy, visual policy, hard filters, soft preferences và intents. Metadata inferred chỉ boost mềm; revision/date verified mới giới hạn hard scope. `resolve_current_scope` chỉ lấy document IDs đủ điều kiện trong scope hiện tại trước khi áp ngày hiệu lực. Thiếu metadata/ngữ nghĩa user choice trả clarification trước retrieval.

`analyze_query` tạo từng requirement cùng wording trong câu hỏi và features calculation/dependency/comparison/visual. Rules chọn direct lookup; câu dài không tự complex. Ambiguous dùng local bounded planner, validate rồi reuse plan nếu chọn research. Route trace ghi method/reason/capability; không có confidence tự khai làm gate.

## Tìm leaf và giữ scope

`run_baseline` nhận cùng plan/analysis/QueryBudget từ dispatcher. `retrieve` tính eligible chunks trước top-k; lexical dùng SQLite FTS5/BM25, dense dùng local TextEncoder/Qdrant. Hybrid hợp bằng RRF `1/(60+rank)`, deduplicate content keys, boost metadata/table khi áp dụng, rerank candidates rồi chọn evidence IDs.

Defaults candidates 20, rerank_candidates 12, evidence_limit 5. Model retrieval/rerank nạp rồi đóng tuần tự. Hard source/document/page/metadata filters giữ ở mọi search, conflict pool và context mở rộng bảng. Source filter và document selector UI được lấy giao; không overwrite để mở scope.

| Retrieval input | Canonical behavior |
|---|---|
| `hybrid`, visual auto | Keyword + semantic, visual eligibility/phase khi cần |
| Legacy strategy `auto` | Hybrid + visual auto |
| Legacy strategy `visual` | Hybrid + visual required |
| `lexical` / `dense` | Debug override nhánh text; visual policy vẫn là field riêng |
| Visual policy `off` | Không gọi phase ảnh |

`detect_conflicts` kiểm tra numeric/version theo metadata verified trong allowed scope; không giải quyết mọi semantic contradiction. Conflict trả nguồn và trạng thái rõ, không escalation để chọn nguồn thuận lợi hơn.

## Shared visual phase sau retrieval

Cả baseline và research gọi `bt7.visual_evidence.run_visual_phase`. Đây là phase đọc evidence, không là route thứ ba. Text đủ không nạp VLM vô ích; policy required, visual intent/hits hoặc OCR/table flags thiếu có thể trigger.

Code chọn assets thật theo SHA từ allowed chunks, kiểm tra content hash và bbox. Tối đa hai images/crops mỗi call. Leaf crop giữ source bbox; partial-page scope không gửi full page khi có pixels ngoài scope hoặc không có crop hợp lệ. Model chỉ nhận image/source/requirement IDs được cấp.

VLM trả observations với state observed/unreadable/ambiguous, literal text/value/unit, entity/row/column labels. Validator kiểm tra IDs, entity, raw literal/unit, task column và provenance; unreadable/ambiguous không được cung cấp usable value. OCR/visual reading khác nhau cho cùng field có thể trả conflict; không âm thầm ưu tiên một phía. Caption index chỉ giúp retrieval, không là text fact.

Cache chỉ trong request, key gồm image/crop/hash, query, requirements/task, scope, model và prompt version. Repair tính vào visual/generation counters. Validated observations vẫn chưa được người review pixel; validation không chứng minh VLM đọc đúng.

## Claims và coverage trong cùng generation

Sau preflight assessment rules, `grounded_requirement_answer` nhận text sources và validated visual observations. Final answering không tự đính ảnh theo branch. Model trả schema:

```json
{
  "status": "answered",
  "claims": [{"text": "Orion voltage is 24 V", "support_refs": [{"source_id": "E1", "quote": "Orion voltage: 24 V"}]}],
  "coverage": [{"requirement_id": "R1", "state": "supported", "support_refs": [{"source_id": "E1", "quote": "Orion voltage: 24 V"}]}]
}
```

Mỗi requirement phải xuất hiện một lần. Supported cần ref/quote đúng source được cấp; image observation chỉ hỗ trợ requirement được gán. Claims phải cite sources đã hỗ trợ coverage. Code render answer/citations từ claims; không giữ một answer text khác model tự viết. Schema/source sai có tối đa một repair trong cùng budget; vẫn sai báo lỗi. Coverage thiếu là thiếu evidence, không giả answered chỉ vì có citation.

Direct numeric claim phải giữ số xuất hiện trong support quotes; quote đúng nhưng claim bịa số bị reject/repair. Với Latest Estimate có currency/entity rõ, source-rule validator có thể cung cấp literal row/column hints cho generation; UNKNOWN ở cột khác không làm ô được yêu cầu trở thành thiếu. Hints không điền ô unknown hoặc đổi trạng thái model bằng code. Forced baseline từ chối arithmetic cần typed calculator; auto có thể chọn research.

`assess_evidence` tạo state sufficient/missing/requires_research/clarification/conflict, missing requirement IDs, next action và validation errors. Sufficient là kết quả kiểm tra hỗ trợ hiện có, không phải xác suất đúng hay semantic accuracy đã soát.

## Escalation và kết thúc

Forced baseline giữ runner để benchmark. Với auto, missing requirements và hướng tìm tiếp có thể chuyển baseline → hierarchical một lần. Cùng question, immutable scope, conditions và QueryBudget đi tiếp; không cấp deadline/calls mới. Conflict, runtime/config lỗi, user choice, hết budget hoặc không có hướng mới không escalation. Research không quay lại baseline để vượt fact gate.

`ask` chỉ công bố AnswerRecord cuối và ghi một turn long-term cho toàn request. Attempts giữ route/status/evidence/chunk IDs, trace stages/timings/usage; routing có initial/final route và escalation reasons. `finally` đóng generator/memory; retrieval resources đóng theo stage. Instant result/pending trong RAM; UI fingerprint corpus/scope/config/memory để không hiển thị cache cũ.

| Kết quả | Ý nghĩa |
|---|---|
| answered | Claims/coverage/source validators hiện có chấp nhận |
| abstain | Thiếu support, không hướng mới hoặc budget/capability dừng |
| clarification | Cần lựa chọn/điều kiện người dùng |
| conflict | Nguồn/visual đọc khác nhau chưa giải quyết |
| exception | Config/model/runtime/schema lỗi, UI/CLI hiển thị |

Một QueryBudget monotonic mặc định 300 giây, 12 logical retrieval calls và 1 escalation, xuyên rewrite đến final generation. Latency gồm model load; inference stage không được coi là instant cancellation tuyệt đối. Pointer/quote/coverage hợp lệ vẫn cần kiểm tra đáp án theo nguồn. Chưa có benchmark human-reviewed toàn corpus để khẳng định baseline nhanh/chậm/chính xác hơn research.

Đọc tiếp: [hierarchical](hierarchical.md), [system architecture](../SYSTEM_ARCHITECTURE.md).
