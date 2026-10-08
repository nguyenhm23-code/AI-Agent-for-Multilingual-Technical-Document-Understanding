# So sánh baseline, hierarchical và auto dispatcher

Ngày: 03/10/2026. Baseline/hierarchical là hai runners được giữ trong code. Production auto chọn runner; UI không yêu cầu người dùng chọn mode/strategy/assumptions. Forced modes qua CLI/config dùng debug/A-B/C. [Báo cáo implementation](rag_architecture/UPDATE_IMPLEMENTATION_REPORT.md) là nguồn kiểm chứng mới; CHANGE_REPORT giữ kết quả trước cập nhật.

## Thay đổi điều phối

| Trước cập nhật | Implementation hiện tại |
|---|---|
| Config/UI chọn baseline/hierarchical; mặc định dataclass baseline | Config default auto; rules trước, ambiguous local planner validated/reused |
| Strategy auto có visual behavior gắn baseline | Hybrid canonical + visual policy riêng; auto/visual aliases rõ |
| Baseline đính ảnh lúc final answering | Shared visual evidence phase sau route/retrieval ở cả runners |
| Research source facts text/table, caption bị loại | Text literal validators + validated source_visual observations/facts |
| Assumptions JSON/default BAT truyền UI→planner→calculator/pending | Không channel/default assumptions; literal question/clarification conditions có U IDs/provenance |
| Baseline một lượt, citation chưa kiểm đủ requirements | Claims+coverage cùng generation, assessment và tối đa một escalation có hướng/budget |
| Pending phụ thuộc mode hierarchical | Pending schema2/scope/content/config, auto instant/long-term |
| Budget research riêng | Một request QueryBudget xuyên rewrite/router/probe/research/repair |

Không đưa VLM thành kiến trúc thứ ba. Keyword/semantic ở BT6; visual phase bổ sung evidence có source image/crop. Câu text đủ support skip VLM. Conditions người dùng khác document facts; không dùng answer/history/default fixture làm evidence.

## Cơ chế từng runner

| Bước | Baseline | Hierarchical |
|---|---|---|
| Scope | Shared QueryPlan hard filters + verified current/revision intersection | Cùng immutable scope, giữ ở routing/escape/crop/parent |
| Retrieval | Một hybrid leaf probe, RRF/rerank/evidence limit | Task document/page→leaf + escape mỗi query, vector reuse |
| Plan | Requirements/features từ analysis, không task planner cho simple rule | Validated T/C/U plan, dependency DAG, reuse router plan |
| Visual | Shared run_visual_phase, observations theo requirements | Cùng phase theo task, parse/unit facts từ observations |
| Support | Claims exact source refs/quotes + coverage từng requirement | Text/visual facts có entity/unit/row/column/source provenance |
| Missing | Auto có thể escalation một lần, forced baseline giữ riêng | Missing query rounds bounded/no_progress; không quay baseline |
| Số học | Direct answer với support validators; không thay typed multi-input calculator | Decimal operations, units/currency/period/division checks |
| Final | Answer/citations render từ claims | Calculation renderer hoặc quoted-fact synthesis/citations |
| Memory | Dispatcher finalization một turn | Cùng dispatcher, không lưu từng attempt riêng |

Hierarchical merge nối routed trước escape, deduplicate chunk IDs/table expansion; chưa joint rerank toàn merged list. Synopsis là routing context, không source fact. Escape vẫn tìm toàn scope mỗi task nên research không được mặc định quảng bá nhanh hơn.

## Ưu điểm và giới hạn

| Nội dung | Điều có thể kiểm tra | Giới hạn còn lại |
|---|---|---|
| Direct lookup | Rule route ít router overhead; claims/coverage/source pointers | Retrieval top-k hoặc requirements parsing/model claims vẫn có thể sai |
| Nhiều inputs/dependency | Typed facts/tasks, source-derived bridge query, bounded missing search | Planner có thể chia thiếu/sai task hoặc source literal đúng nhưng sai ngữ nghĩa |
| Phép tính | Decimal/units/currency and separate condition provenance | Business relationship không suy chỉ từ compatible units; parser hữu hạn |
| Visual | Actual allowed source/hash/crop, observation IDs/unit/entity checks | Validation không chứng minh pixel/model reading đúng; human source review cần thiết |
| Scope/local policy | Hard filters mọi paths, loopback/model guard, no auto cloud fallback | Qdrant single-process, chưa multiuser ACL |
| Budget | Không renew calls/deadline/escalation; repairs/counters traced | Stage inference không guarantee tức thì dừng mọi GPU operation |
| Confidence | Missing/coverage/status/stop reason rõ | Chưa calibrated confidence 99% hoặc corpus accuracy/SLA |

Conflict/config/runtime/user choice/budget không tự đổi route để tìm đáp án thuận lợi. Research hết support không chạy baseline bypass. Conditions không giả citation nguồn. Model answer history chỉ giải quyết tham chiếu.

## BAT và nguồn kịch bản

Hoàn thành 100% mục tiêu giảm illicit volume khác quan hệ transfer thành BAT sales. Ca thiếu transfer cần source relation hoặc clarification/abstain; đủ volume/rate/cost không tự cho phép tính revenue. Ca nói rõ transfer trong question có U condition ID/span/origin/applies_to, document inputs vẫn có citations riêng. Không giữ kết quả cũ bằng hidden JSON assumption.

Các kết quả numeric/demo latency trước update nằm trong CHANGE_REPORT/artifacts lịch sử; không đem làm score auto mới. Baseline/hierarchical/auto phải dùng cùng ready snapshot, question/scope/model/device/evidence budget để so sánh.

## Đánh giá A/B/C

Forced CLI --mode baseline/hierarchical/auto dùng cùng configs/rag.local.json; chỉ mode/output khác. Multidoc evaluator giữ mode, cần reviewed gold; acceptable_routes cho phép nhiều route đáp đúng. Gold labels không là inference input.

Metrics ghi routing initial/final/agreement, escalation, final status/evidence-set/calculation correctness, latency P50/P95/stages, logical/model calls, tokens/budget exits. Pointer validity/substring là proxies riêng, không semantic/citation correctness. Visual observation correctness cần đối chiếu image/crop thật. Unit/scripted/AppTest khác real model inference; cold model loads khác steady-state/warm serving.

Chưa có human-reviewed toàn-corpus A/B/C đủ để kết luận route mới nhanh/chính xác hơn. Report giới hạn bộ ca demo và không dùng route accuracy che answer regression.

## Migration và thao tác

Production local config auto/hybrid/visual auto/local_only; UI ép bằng replace. `rag.baseline.json`/`rag.flat.json` forced baseline, `rag.hierarchical.json` forced research để benchmark. Legacy strategy auto→hybrid+visual auto, visual→hybrid+visual required; không image embeddings mới.

Index1.0 direct baseline vẫn chạy; complex research capability_missing rõ. Nâng bằng rag-upgrade output mới hoặc chọn hierarchy ready có sẵn, không mutate source manifest/snapshot/locks. Pending2 kiểm scope/content/config, legacy rejected nhưng history giữ. Assumptions CLI/config/fixtures cũ có migration error; file BAT giữ nhãn lịch sử và product không đọc.

Xem [system](SYSTEM_ARCHITECTURE.md), [demo/code flow](DEMO_CODE_FLOW.md), [baseline](rag_architecture/baseline.md), [hierarchical](rag_architecture/hierarchical.md).
