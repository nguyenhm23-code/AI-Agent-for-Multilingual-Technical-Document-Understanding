# Báo cáo cập nhật RAG theo UPDATE_IMPLEMENTATION_PLAN

Thực hiện ngày 03–04/10/2026, theo [plan](UPDATE_IMPLEMENTATION_PLAN.md). Báo cáo này ghi implementation, kiểm chứng và những lỗi thực sự phát hiện trong quá trình chạy. Kết quả hiện tại: **173 test pass; auto đã demo thành công tra cứu Việt/Anh, đọc ảnh, phép tính nhiều nguồn, phép tính từ ảnh và từ chối khi thiếu nguồn/quan hệ nghiệp vụ**. Số liệu A/B/C cuối được ghi ở mục 4.

Các demo có chạy model local thật. Bộ nguồn nhỏ được agent soát, chưa là gold doanh nghiệp được con người duyệt hoặc holdout toàn corpus. Không diễn giải các kết quả này thành accuracy 99% hay chứng nhận rollout.

## 1. Những gì đã làm

| Hạng mục | Thay đổi đã triển khai | Bằng chứng chính |
|---|---|---|
| F1 — Tự định tuyến | `auto` mặc định; rules Việt/Anh trước, local planner khi ambiguous; plan validated được reuse | `test_query_router.py`, `test_hierarchical.py`; trace demo có initial/final route và reason |
| F2 — UI đơn giản | Bỏ selector kiến trúc, strategy và ô assumptions; giữ scope, filters nâng cao, memory, citation/trace | Sáu UI AppTest trong `test_ui_auto.py` |
| F3 — Bỏ assumptions JSON | Xóa tham số xuyên `ask`, planner, runner, calculator, UI, evaluator và pending; CLI/config cũ báo migration | `test_question_conditions.py`, `test_rag_interfaces.py`, `test_pending_clarification.py` |
| F4 — Hybrid mặc định | Keyword + semantic ở BT6; legacy strategy `auto` → hybrid/visual auto, `visual` → hybrid/visual required | Alias tests, trace `requested_strategy/effective_strategy/visual_policy` |
| F5 — VLM dùng chung | `run_visual_phase` sau route/retrieval cho cả runners; chỉ phase này gửi ảnh; research dùng được `source_visual` facts | Demo 24 V và 24 − 12 = 12 V; mixed text/image calculator test |
| F6 — Escalation hữu hạn | Baseline → research tối đa một lần, giữ scope và một QueryBudget; không đổi route khi conflict/error/user choice/hết budget | `test_auto_rag.py`, `test_dispatcher_review.py` |
| F7 — Trace | Route/reason/capability, attempts, evidence IDs, stop reason, tổng counters/tokens và stage timings | JSON demo; evaluator routing metrics tests |
| F8 — Dữ kiện có nguồn | Structured claims/coverage; text quote/value/entity/row/column checks; image hash/crop/observation/unit checks; Decimal calculator | Source/citation/provenance tests; các demo có facts và nguồn cụ thể |

Luồng chính hiện tại:

```text
UI/CLI → ask → kiểm tra local policy/index/scope/pending
       → resolve tham chiếu → plan_query/current scope → analyze_query/router
       → baseline: hybrid retrieval → shared visual → claims/coverage/assessment
       → research: validated plan → hierarchical + escape → shared visual
                   → text/image facts → coverage/conflict → calculator/synthesis
       → final trace, một turn memory, đóng tài nguyên
```

Thêm contracts chung trong [contracts.py](../../../denso_a3/contracts.py), tách [baseline_runner.py](../../../denso_a3/pipeline/baseline_runner.py), [query_budget.py](../../../denso_a3/pipeline/query_budget.py), [question_analysis.py](../../../denso_a3/bt6/question_analysis.py), [query_router.py](../../../denso_a3/bt6/query_router.py), [evidence_assessment.py](../../../denso_a3/bt6/evidence_assessment.py), [question_conditions.py](../../../denso_a3/bt6/question_conditions.py) và [visual_evidence.py](../../../denso_a3/bt7/visual_evidence.py). Dispatcher/research, adapters, retrieval, answering/fact extraction/calculator/memory, UI/CLI/config/evaluators và fixtures đã được sửa theo cùng contracts.

`ask` không còn `assumptions`; callers dùng keyword arguments. API provider bị chặn trước memory rewrite; local adapter không dùng proxy môi trường, chỉ loopback, không theo redirect ra endpoint khác. Không download model trong query. Adapter API cũ được giữ cho code legacy gọi trực tiếp, không phải cloud fallback của sản phẩm.

Mặc định một query: 300 giây, 12 logical retrieval calls, một escalation, tối đa hai router attempts gồm repair, sáu visual calls gồm repair, hai images/call. Research giữ sáu tasks/ba rounds. Một task retrieval bao gồm routed và escape; counter này khác số backend searches. Deadline/calls không được cấp lại khi chuyển runner. GPU stage đang chạy không có cam kết hủy tức thì.

Conditions chỉ từ literal question/clarification: U ID, raw text, span, origin, applies_to và normalized value/unit. Phần trăm có trace chia 100; source facts/citations và condition IDs tách riêng. “Hoàn thành 100% mục tiêu” không tự thành “100% lượng giảm chuyển sang BAT sales”. Thiếu quan hệ này được hỏi làm rõ trước model I/O; nếu người hỏi yêu cầu tìm sourced factor thì có đường task tìm nguồn.

Pending schema 2.0 giữ câu gốc và các câu làm rõ tích lũy, scope/content/config fingerprints. Legacy assumptions/pending hoặc scope thay đổi bị invalidated; history giữ lại. Facts từ câu trả lời cũ không được dùng làm nguồn. Current/revision, UI document selector và CLI `--source` lấy giao với filters gốc. Crop dùng ceil góc trái trên/floor góc phải dưới để không gửi thêm pixel ngoài phần cho phép.

## 2. Migration và tài liệu

Workspace trước update trỏ index schema 1.0 `.local/indexes/59f1648f8fd3`. Đã chọn snapshot ready `.local/indexes/hierarchical_20261003`: **schema 1.1, hierarchy 1.0, 1.217 chunks, 173 nodes**. Giữ snapshot cũ, không OCR/re-embed leaf hay sửa manifest nguồn. Selector cũ được lưu ở `.local/workspace.pre_auto_update.json`; [migration artifact](../../../results/auto_update_migration.json) ghi snapshot được chọn. Long-term history không bị xóa.

[README](../../../README.md), [SYSTEM_ARCHITECTURE](../SYSTEM_ARCHITECTURE.md), [DEMO_CODE_FLOW](../DEMO_CODE_FLOW.md), [ARCHITECTURE_COMPARISON](../ARCHITECTURE_COMPARISON.md), [baseline](baseline.md) và [hierarchical](hierarchical.md) đã cập nhật entry point, thứ tự hàm, contracts, migration, scope/coordinates, budget và giới hạn. `configs/bat.assumptions.json` chỉ giữ nhãn lịch sử; sản phẩm không đọc nó. CHANGE_REPORT cũ được giữ như artifact lịch sử.

## 3. Test đã chạy

Trước khi sửa: **31 test pass** trong `test_rag.py` + `test_hierarchical.py`. Đây là baseline regression, không phải số đo latency/accuracy của code cũ.

Lượt regression cuối:

```powershell
& ./.venv/Scripts/python.exe -m pytest -q --junitxml=results/auto_update_tests.xml
# 173 passed in 26.93s
```

[JUnit kết quả](../../../results/auto_update_tests.xml). Toàn suite gồm extraction regressions, tests RAG cũ và các ca mới. Unit/integration tests dùng model có kịch bản để cố ý đưa dữ liệu sai, kiểm tra từ chối/repair/budget/scope. UI dùng Streamlit AppTest và scripted answers. Các tests này không được tính là benchmark model thật.

Ma trận đối chiếu T01–T31 của plan; các ca dưới đây đều pass trong suite:

| ID | Ca đã kiểm chứng | Test/bằng chứng |
|---|---|---|
| T01 | Tra thông số Việt/Anh, rules không gọi classifier | `test_rules_need_no_classifier`; real `simple_en`, `simple_vi` |
| T02 | Câu dài chỉ hỏi một fact vẫn baseline | Ca dài trong `test_rules_need_no_classifier` |
| T03 | Dependency dùng giá trị nguồn thay placeholder | `test_bridge_cross_document_and_bounded_missing` |
| T04 | Tách requirements nhiều thuộc tính; citation chưa đủ coverage | `test_independent_attributes_are_separate_requirements`, `test_answer_citation_is_not_enough_when_requirement_missing` |
| T05 | Nhiều inputs, Decimal/currency/per-mille và nguồn | `test_bat_decimal_currency_and_per_mille`, `test_five_inputs_across_files_and_candidate_conflict`; real GBP100 + GBP200 |
| T06 | Ambiguous local plan, bounded repair, reuse/revalidate | `test_ambiguous_local_plan_reused_and_bounded_repair`, `test_research_reuses_validated_plan_without_planner_model_call` |
| T07 | Baseline shared phase đọc ảnh thật, final không đính ảnh lại | `test_answer_visual_support_has_source_crop_and_no_final_images`; real `visual_simple`, UCSD |
| T08 | Research image fact có unit/source, dùng trong calculator | `test_research_mixed_image_text_fact_calculator_demo`; real hai ảnh 24 V/12 V |
| T09 | Routed/escape và parent giữ filters, escape tìm được nguồn fixture | `test_routing_parent_and_escape_never_remove_filters` |
| T10 | Một escalation, một budget, một turn memory | `test_escalation_one_budget_one_memory_turn` |
| T11 | Không có nguồn, dừng hữu hạn | Bridge bounded-missing tests, `test_call_budget_does_not_claim_complete_answer`; real `missing` |
| T12 | Scope source/document/page, ảnh/citation/crop đúng phạm vi | Retrieval prefilter, UI/CLI intersection, image hash/scope tests |
| T13 | Current/revision giao với document/page explicit | `test_current_scope_keeps_document_revision_and_page_intersection`, explicit revision conflict test |
| T14 | Numeric/version/source-image conflict giữ nguồn, không bypass | Numeric conflict tests, `test_visual_ocr_disagreement_is_explicit_conflict` |
| T15 | API/local runtime lỗi không cloud fallback | `test_api_rejected_before_memory_or_generation`, real adapter redirect/health tests |
| T16 | BAT thiếu transfer không tự dùng 100% | `test_gross_margin_needs_sales_relationship_even_with_all_units`, completion-vs-transfer test; real BAT clarification |
| T17 | Condition literal question/clarification, provenance U riêng | `test_literal_percentage_span_and_separate_provenance`, multi-turn pending condition tests |
| T18 | Model invented condition/span/relation bị reject | `test_forged_span_value_and_relation_rejected`, `test_local_planner_repair_rejects_model_invented_condition_and_counts_budget` |
| T19 | Pending legacy/scope khác invalidated, history giữ | `test_pending_legacy_invalidated_without_losing_history`, long-term scope-change test |
| T20 | Hết deadline/calls sau probe không cấp ngân sách mới | `test_exhausted_probe_has_no_new_research_budget`, `test_exhausted_retrieval_budget_does_not_start_new_research_stage` |
| T21 | Citation đúng pointer nhưng sai entity/row hoặc thiếu requirement bị reject | M1/M10, Orion/Vega, cross-row table tests; numeric claim 999 với quote 24 bị repair |
| T22 | Context guards, malformed JSON/repair tính budget | Planner/synthesis context guard, malformed planner/visual JSON, visual context reserve tests |
| T23 | Instant/long-term clarification; không tin lại facts cũ | Pending tests parametrized memory modes, cumulative 50% then currency clarification |
| T24 | Legacy index đơn giản dùng baseline; complex capability_missing | `test_legacy_index_research_capability_missing`, baseline small-context compatibility tests |
| T25 | UI không mode/strategy/assumptions/BAT reader; scope/memory/citation còn | Sáu tests trong `test_ui_auto.py` |
| T26 | Forced CLI modes, legacy assumptions/config migration | `test_rag_interfaces.py`, config validation tests |
| T27 | Chặn outbound trước rewrite; request/repair/tokenizer/health loopback | `test_local_generation_policy.py`, intercept transport của adapter thật |
| T28 | Unreadable/ambiguous/sai OCR/sai unit không usable fact | Visual state/unit/conflict tests |
| T29 | Partial-page không gửi pixel ngoài scope; bbox giữ source frame | `test_partial_page_sends_only_allowed_pixels`, fractional crop/subpixel tests |
| T30 | Hai ảnh/call, repair budget, cache đúng task/scope/model/request | `test_visual_call_has_at_most_two_real_images`, visual repair/cache tests |
| T31 | Text đủ skip VLM | `test_text_only_does_not_load_vlm`; real simple text trace `visual_calls=0` |

Các file test chính: [router](../../../tests/test_query_router.py), [auto](../../../tests/test_auto_rag.py), [conditions](../../../tests/test_question_conditions.py), [visual](../../../tests/test_visual_evidence.py), [pending](../../../tests/test_pending_clarification.py), [scope review](../../../tests/test_dispatcher_review.py), [baseline support](../../../tests/test_baseline_support.py), [local policy](../../../tests/test_local_generation_policy.py), [UI](../../../tests/test_ui_auto.py), [interfaces/evaluator](../../../tests/test_rag_interfaces.py).

## 4. Demo model local thật và so sánh A/B/C

Môi trường: Python 3.12.14, NVIDIA RTX 4050 Laptop GPU (6.141 MiB VRAM), driver 596.21; embedding BAAI/bge-m3; Qwen3-VL-2B-Instruct Q4_K_M GGUF + mmproj Q8_0, llama.cpp b11235. Weights đã có local. Stages nạp/đóng model tuần tự; thời gian gồm load. OS disk cache có thể ấm, không gọi đây là steady-state model inference.

[Script demo](../../../evaluation/demo_auto_update.py) tạo năm source PNG có literal đọc được, build vectors/hierarchy bằng embedding thật; caption của hai source visual không chứa số đo. Expected labels chỉ dùng kiểm tra sau `ask`, không truyền vào inference. Các modes chạy cùng snapshot/filters/model/budgets, `rerank=False`, candidates/rerank_candidates=8, evidence_limit=5, max_answer_tokens=1000. Vì demo dùng cấu hình này, không lấy latency của nó đại diện cấu hình production mặc định `rerank=True`.

| Ca | Nguồn và kết quả yêu cầu | Auto |
|---|---|---|
| `simple_en` | Text M1: rated voltage 24 V | baseline, answered 24 V |
| `simple_vi` | Cùng nguồn, hỏi “Điện áp định mức của M1…” | baseline, answered 24 V |
| `calculation` | Alpha GBP100, Beta GBP200; hỏi tổng | hierarchical, calculator đúng 300 GBP, hai source facts |
| `visual_simple` | Ảnh M1 có 24 V, caption không có con số | baseline + shared visual, answered 24 V |
| `visual_calculation` | Hai ảnh M1 24 V/M2 12 V; hỏi M1 − M2 | hierarchical + shared visual, calculator đúng 12 V; hai source_visual facts với image/observation IDs |
| `missing` | Scope không chứa nguồn | abstain, không bịa giá trị |

Các phép tính được kiểm tra bằng Decimal result, số lượng inputs và provenance facts; không chỉ kiểm tra substring. Trạng thái, route và answer literal của các ca còn lại là checks demo; đã đọc ảnh nguồn để soát 24/12 V. Bộ này nhỏ và đã dùng trong lúc sửa prompt/rules, không phải holdout.

[18 records A/B/C cuối](../../../results/auto_update_final_abc/summary.json), [metrics từ trace](../../../results/auto_update_acceptance_metrics.json):

| Mode | Đạt mục tiêu ca | P50 / P95 wall seconds | Logical retrieval | Planner / router calls | Generation attempts, gồm visual | Visual calls | Tổng tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| Forced baseline | 4/6 | 10,87 / 17,79 | 6 | 0 / 0 | 6 | 3 | 6.002 |
| Forced hierarchical | 6/6 | 22,04 / 23,33 | 8 | 6 / 0 | 18 | 3 | 11.392 |
| Auto | 6/6 | 10,79 / 23,12 | 8 | 2 / 0 | 12 | 3 | 9.072 |

P95 trong bảng dùng numpy linear interpolation của evaluator. `summary.json` demo còn lưu nearest-rank P95; với n=6, đó là max mẫu nên khác bảng. Zero runtime/schema errors, zero budget exits trong 18 ca cuối; không có escalation trong bộ real này. Escalation/dependency/repair/cache/condition continuation được ép tình huống bằng integration tests, không tuyên bố chúng đã phát sinh ở sáu ca real.

Auto route agreement là 6/6 trên nhãn demo agent soát; câu rules không có LLM routing call. Hai phép tính dùng hai planner calls tổng cộng. Có thêm kiểm chứng local router ambiguous riêng bên dưới. Forced modes là override debug, không dùng agreement của chúng làm độ đúng classifier.

Ca ambiguous model thật riêng: “Analyze the Latest Estimate costs of Alpha and Beta in GBP.” → `method=local_llm`, hierarchical, `plan_reused=true`, hai router attempts gồm repair, **planner_calls=0**, hai retrieval, answered Alpha GBP100 và Beta GBP200 với hai nguồn. Planning ở runner bằng 0 vì dùng plan router; wall 52,15 s. [Record local router/reuse](../../../results/auto_update_ambiguous_local.json). Ca này nằm ngoài sáu mẫu A/B/C và không được cộng vào percentiles ở bảng trên.

| Stage auto, sáu ca | P50 / P95 (ms) |
|---|---:|
| Routing rules | 0,027 / 0,036 |
| Planning | 0 / 7.271,62 |
| Retrieval | 3.593,98 / 7.260,62 |
| Visual phase | 0,094 / 8.153,95 |
| Evidence assessment | 0,045 / 0,239 |
| Generation | 3.676,21 / 7.630,99 |

Percentiles từng stage có mẫu skip bằng 0, không cộng các percentiles thành total. Trace từng attempt và tokens/counters nằm trong metrics artifact. Auto wall times: 9,87 s / 9,72 s / 23,13 s / 11,70 s / 23,10 s / 0,01 s theo thứ tự bảng ca.

Forced baseline từ chối hai phép tính với `calculator_required`; đây là giới hạn runner được giữ cho debug. Chấm theo mục tiêu “trả được kết quả tính” thì baseline thiếu 2/6 ca, nhưng không trả đáp số không có calculator. Auto chọn research và trả đúng hai ca này. Vì tập chỉ có sáu câu và thứ tự load khác nhau, không kết luận auto/hierarchical nhanh hơn trên toàn corpus.

Kho tài liệu thật, snapshot 1.217 chunks:

| Ca | Kết quả đã quan sát và soát nguồn |
|---|---|
| `corpus_budget` | “The Latest Estimate cost for Jessie is GBP469,000.” Citation `nhwc0428.pdf`, trang 11, đúng ô Latest estimate ở dòng Jessie; cột Variance trống không làm ô này thiếu |
| `corpus_visual` | “UNIVERSITY OF CALIFORNIA, SAN DIEGO”, từ shared VLM trên `images/24580.png`; có visual citation/observation |
| `corpus_bat_missing_transfer` | Clarification: phần giảm illicit-trade chuyển thành BAT sales theo tỷ lệ nào/tài liệu nào xác nhận? Không retrieval/generation/calculator, không tự gán tỷ lệ |

[Production demo records](../../../results/auto_update_production_verified/summary.json): 3/3 ca đạt; 18,98 s / 13,54 s / 0,01 s lần lượt. Agent đã mở ảnh canonical trang 11 và ảnh UCSD để soát các đáp án trên; đây chưa là human-reviewed corpus gold.

Đã chạy thêm cùng câu Jessie bằng **nguyên cấu hình `configs/rag.local.json`, `rerank=True`**, không dùng overrides của demo: answered GBP469,000, một retrieval, một generation, zero router/visual/escalation, wall 28,06 s. [Artifact production defaults](../../../results/auto_update_production_defaults_budget.json). Con số khác demo do cấu hình/cold-load; một mẫu không phải benchmark SLA.

Lệnh tái hiện từ root project; dùng thư mục output mới để không đọc lại các records đã có:

```powershell
# Build nguồn demo/vector/hierarchy một lần rồi dùng cùng snapshot cho A/B/C
& ./.venv/Scripts/python.exe evaluation/demo_auto_update.py --output results/my_auto_demo
# Chạy lại trên snapshot đã tạo, output mới
& ./.venv/Scripts/python.exe evaluation/demo_auto_update.py --index results/my_auto_demo/snapshot --modes auto --output results/my_auto_rerun
# Ba ca trên kho thật
& ./.venv/Scripts/python.exe evaluation/demo_auto_update.py --production --modes auto --output results/my_source_demo
# Mở UI local
& ./.venv/Scripts/python.exe -m denso_a3 rag-ui
```

## 5. Vì sao lúc đầu chưa được, đã sửa thế nào

Không chỉ chạy tests rồi đánh dấu hoàn tất. Demo local đã phát hiện các lỗi dưới đây; artifacts thất bại được giữ riêng, không ghi đè bằng kết quả pass.

| Lỗi ban đầu | Nguyên nhân quan sát | Cách giải quyết / kiểm chứng |
|---|---|---|
| Planner hỏi Alpha/Beta nhưng tìm Atlas | Model 2B sao chép entity trong few-shot có nội dung | Bỏ example entity/value, dùng format trống; validate entity literal hoặc sourced dependency. Demo arithmetic cuối đúng Alpha/Beta |
| Planner tạo U1 từ “clarification” chưa hề có | Model tự đề xuất scenario/nguồn provenance | Conditions chỉ từ literals code đã validate; grammar maxItems=0 khi không có condition, enum literal/origin/ID khi có; invented condition tests |
| Hỏi tính tổng nhưng trả tasks mà không calculation | Schema cũ cho calculations rỗng hoặc task T3 “calculate total” với một operand | Arithmetic grammar bắt buộc operation hoặc genuine clarification, IDs/DAG/typed operands; validator recheck cả plan reused; exact pair không thiếu Alpha/Beta |
| Planner bỏ một entity hoặc dùng T1 hai lần | Citation/input count chưa bảo vệ ý định cặp entity | Validate literal named pair và dùng đủ operands; duplicate operand chỉ được nếu question nói doubling |
| Phép tính đọc ảnh bị missing | VLM trả raw value kèm entity phrase, thiếu unit hoặc label là page ID | Grammar numeric literal + visible unit, entity/field labels, schema observed/unreadable riêng; actual image calculator 24 − 12 = 12 V |
| Text “5 mm/2 MPa” bị scale parser hiểu nhầm | Suffix một ký tự `m` ăn prefix đơn vị vật lý | Parse scale có boundary/điều kiện, giữ m/mm/MPa; tests normalize physical units và aliases |
| Một pixel ngoài crop được gửi | Làm tròn crop mở rộng biên fractional | ceil left/top, floor right/bottom; zero allowed pixel → missing, không model call; colored-pixel crop tests |
| Có citation hợp lệ nhưng sai entity/row | M1 khớp M10 hoặc quote rộng chứa field của dòng M2 | Token boundary entity và table row/column binding; wrong named entity/cross-row tests |
| Jessie có ô tiền đọc được nhưng model abstain | Model thấy UNKNOWN ở cột khác và thiếu hint row/column | Reuse source-rule validator tạo literal table hints cho đúng requirement; không điền UNKNOWN; source demo trả GBP469,000 |
| Hierarchical tiếng Việt bỏ qua “24 V” | Planner gán nominal voltage task là text | Grammar/validator gán direct voltage/pressure request thành quantity, giữ text bridge/definition hợp lệ; actual forced hierarchical Việt đã pass |
| Pending cũ có assumptions hoặc scope mới vẫn có thể tiếp tục | Pending gắn mode, thiếu schema/content/config guards | Pending2, kiểm fingerprints, tích lũy literal clarification, fresh retrieval; giữ history nhưng không dùng old facts |
| API/proxy/redirect có thể rời local path | Guard chỉ ở một số callers hoặc session dùng môi trường | Chặn provider trước rewrite và trong adapter; trust_env=false, reject redirect, bind 127.0.0.1; transport interception tests |
| UI tests ban đầu tương tác sai rerun | Dùng AppTest set_value khi chưa rerun session/widget state | Chỉnh trình tự thao tác UI rồi assert kết quả thật; sáu AppTest pass |

Các folders chẩn đoán như `results/auto_update_demo`, `_v2`…`_v6`, `auto_update_final_demo`, `auto_update_acceptance_demo`, `auto_update_source_acceptance` và `auto_update_abc_verified` là các lượt trước khi sửa xong. Riêng `auto_update_abc_verified/simple_vi_hierarchical.json` giữ lỗi text/quantity. Kết quả nghiệm thu mới dùng `auto_update_final_abc`, JUnit cuối và source/default-config records được dẫn trong báo cáo. Những lượt FAIL cũ không được tính vào thống kê pass cuối.

## 6. Phạm vi đã xác nhận và phần chưa xác nhận

Implementation và các ca demo chức năng đã được thực hiện; T01–T31 có regression coverage. Trace/evaluator đã ghi routing/counters/tokens/timings và kiểm expected status/evidence/calculation/condition provenance. Forced baseline an toàn từ chối arithmetic; auto dùng calculator đúng nguồn.

Chưa có latency/accuracy model-real của **code trước update** đo trên cùng bộ ca: baseline đầu chỉ là 31 regression tests. Các lượt FAIL trong quá trình sửa là diagnostics của implementation đang phát triển, không được gọi là before/after benchmark code cũ. P50/P95 hiện tại là số đo sau update trên demo nhỏ.

Chưa có gold toàn corpus được con người soát, tune/holdout riêng hoặc SLA. Router/requirements/LLM decomposition và pixel reading vẫn có thể sai dù quote/pointer/schema hợp lệ. Parser condition/unit hữu hạn; prose scenario mơ hồ hoặc unit chưa hỗ trợ bị từ chối, không tự quy đổi thành hệ số. Chưa chạy phép tính BAT đầy đủ có explicit transfer bằng model thật trên toàn nguồn trong lượt nghiệm thu này; source/condition arithmetic và multi-turn clarification đã có scripted tests, không mượn đáp số lịch sử để giả demo mới.

Qdrant local vẫn một process/index; UI AppTest là kiểm chứng hành vi UI với model có kịch bản. Các artifacts trong `results/` và `.local/` bị gitignore, tồn tại trên workspace này; script/tests/docs là phần mã để tái hiện. [Manifest nghiệm thu](../../../results/auto_update_acceptance_manifest.json) lưu SHA-256 của 85 source/test/config files cùng test counters và danh sách demo artifacts. Không tạo commit/PR hoặc công bố kết quả ra dịch vụ ngoài.
