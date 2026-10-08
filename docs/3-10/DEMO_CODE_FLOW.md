# Demo và luồng code auto RAG

Ngày: 03/10/2026. Lệnh dưới đây dùng contract hiện tại. [Báo cáo implementation](rag_architecture/UPDATE_IMPLEMENTATION_REPORT.md) ghi artifact và kết quả thực sự; lệnh ví dụ không tự là evidence đã chạy. Các kết quả BAT/bridge trước cập nhật trong CHANGE_REPORT là lịch sử.

## Chuẩn bị và mở UI

Từ D:/Denso/project với Python .venv; weights/runtime đã bootstrap trong .cache/rag. Snapshot hierarchy ready hiện có .local/indexes/hierarchical_20261003 dùng cùng corpus cho baseline/hierarchical/auto. Query không tự build/download.

```powershell
$env:PYTHONUTF8 = '1'
& ./.venv/Scripts/python.exe -m denso_a3 rag-ui
# Chỉ khi cần nâng index cũ: output phải mới
& ./.venv/Scripts/python.exe -m denso_a3 rag-upgrade --index .local/indexes/59f1648f8fd3 --output .local/indexes/new_hierarchy_demo --config configs/rag.hierarchical.json
```

Trong UI nhập snapshot ready và configs/rag.local.json, bấm Mở index. Nhập câu hỏi, chọn toàn kho/tài liệu, instant/long-term; advanced hard filters chỉ dùng metadata verified. UI không cần chọn kiến trúc/strategy hoặc JSON assumptions. Chi tiết answer mở source image/page/crop pointers, route reason, coverage/budget và full trace.

UI áp replace(config, orchestration_mode=auto, retrieval_strategy=hybrid, visual_policy=auto, local_only=True), gọi ask bằng keyword. Upload chạy extraction/build subprocess; snapshot mới được activate, không mutate source. Qdrant local chỉ một process mở cùng snapshot; tránh đồng thời UI query và CLI query.

## Các ca demo cần đối chiếu

Demo đã chạy 03–04/10/2026: [script tái hiện](../../evaluation/demo_auto_update.py), [18 records A/B/C](../../results/auto_update_final_abc/summary.json) và [báo cáo](rag_architecture/UPDATE_IMPLEMENTATION_REPORT.md). Auto/hierarchical đạt sáu ca; forced baseline trả được bốn ca và từ chối hai phép tính. Bộ này do agent soát và đã dùng lúc tune, chưa là human-reviewed holdout. Dùng output directory mới khi chạy lại.

| Ca | Input mẫu | Kết quả mong đợi / kiểm nguồn |
|---|---|---|
| Direct EN/VI | Orion voltage? / Điện áp Orion? | Rules baseline, không classifier call, có value/unit/source đúng scope |
| Câu dài một fact | Thêm yêu cầu diễn đạt/source vào direct lookup | Không research chỉ vì độ dài |
| Nhiều thuộc tính | Orion voltage and weight? | Coverage đủ hai yêu cầu hoặc một escalation, không answered thiếu requirement |
| Dependency | Find Orion manufacturer, then manufacturer cost | Research; task sau thay tên bằng validated source fact |
| Calculation | Sum Alpha and Beta budgets | Typed inputs/units, Decimal, citations từng nguồn |
| Simple visual | Read Orion voltage from the diagram | Baseline rồi shared visual phase nhận ảnh nguồn/crop hợp lệ |
| Complex visual | Read two chart costs and calculate difference | Research gọi cùng phase, source_visual facts dùng calculator được hoặc báo thiếu/conflict |
| Thiếu nguồn | Unknown device / bỏ source required khỏi filter | Hữu hạn calls; abstain/stop reason, không đoán hoặc mở scope |
| OCR/ảnh đọc khác | Cùng field có hai readings | Conflict/source rõ, không tự ưu tiên reading thuận lợi |
| Clarification | Câu thiếu lựa chọn/relationship | Natural-language question, pending version 2.0; reply replan trong same scope |

Fixture routing tại evaluation/fixtures/routing_scenarios.json và BAT tại evaluation/fixtures/bat_scenario.json là regression scenarios/draft, không corpus semantic accuracy gold đã được người soát.

## BAT: thiếu transfer và điều kiện explicit

```powershell
$demo = Get-Content evaluation/fixtures/bat_scenario.json -Raw -Encoding utf8 | ConvertFrom-Json
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question $demo.questions[0].question --config configs/rag.local.json --output results/demo_auto/bat_no_transfer.json
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question $demo.questions[1].question --config configs/rag.local.json --output results/demo_auto/bat_explicit_transfer.json
```

Câu thứ nhất nói hoàn thành 100% mục tiêu giảm illicit volume. Nếu tài liệu không nêu transfer relationship, hệ thống cần sourcing task/clarification/abstain; không mặc định illicit reduction bằng BAT sales. Câu thứ hai nói rõ transfer trong question; condition validator giữ U ID/raw span/origin/applies_to và normalized ratio. Năm document inputs volume Jessie/Dawn, margin rate và hai Latest Estimate costs vẫn phải lấy nguồn, đúng row/column/unit/currency. Condition không có document citation giả.

Các số 600m/620m sticks, GBP8.6 per mille, GBP469,000/GBP1,103,000 trong artifact cũ là dữ liệu minh họa đã có nguồn ở demo lịch sử; phải validate lại nguồn/current scope khi query mới. Không lấy answer cũ hoặc fixture expected value làm inference input để bảo đảm ra đáp số.

## Thứ tự hàm theo route

```text
UI/CLI → rag_runner.ask
  config.validate/local policy; manifest ready/schema; fingerprints; QueryBudget
  pending reply hoặc memory rewrite
  plan_query → resolve_current_scope → analyze_query → route_question

baseline:
  run_baseline → RetrievalStore/retrieve → conflict gate
  run_visual_phase (có trigger) → assess_evidence
  grounded_requirement_answer → claims/coverage/source validators
  missing + hướng mới + còn budget → một lần run_research

research:
  run_research → build_research_plan hoặc validate/reuse router plan
  task đủ dependency → retrieve_hierarchical
    allowed → encode vector → document/page rank → routed leaf + escape
  run_visual_phase → extract_fact/validate_fact → check_coverage/fact_conflicts
  missing-query task mới trong round/call/deadline budget
  calculate(Decimal) hoặc grounded_fact_answer → citations

finalize:
  attempts/routing/budget/usage/timings → AnswerRecord
  append một long-term turn/pending → close resources → UI/CLI
```

Baseline generation trả status/claims/coverage cùng một call, không thêm LLM checker riêng. Text support quote phải thuộc sources; image support chỉ từ observation được phase validate. Final answering nhận support text, không nhúng ảnh lại theo baseline branch. Rerank/routed+escape/model stages hiện tuần tự; merge không joint rerank sau nối.

## Dữ liệu, side effects và lỗi

ask signature hiện tại: index/question/config, optional strategy/filters/conversation_id/memory_path/pending/progress; callers keyword. Không assumptions arg. Query không ghi vectors/index hoặc rebuild; long-term ghi .local/conversations.sqlite, instant session RAM. CLI --output chỉ ghi khi được chọn; output evaluation là directory mới.

AnswerRecord có statuses answered/abstain/clarification/conflict, source citations/evidence/conflicts/trace/timings/usage. Config/runtime/model/schema lỗi hiển thị lỗi, không cloud fallback hoặc research→baseline bypass. Hierarchy missing cho complex query trả capability_missing; direct baseline vẫn dùng legacy ready index.

QuestionCondition offsets nằm trong actual question/clarification, không trong prompt rewriting; U IDs document-free. Citation bbox normalized source canonical page, top-left, PDF page từ 1; crop/source image SHA/code-selected pointer không do model đề xuất. Visual state unreadable/ambiguous không có usable value. Pointer/quote/format checks không thay human source review.

Pending version 2.0 cần scope/content/config fingerprints; old assumptions/version bị reject và yêu cầu đặt lại câu đầy đủ, history giữ. UI đổi corpus/filter/config/memory invalidates result/instant pending; long-term pending do dispatcher validate. Đổi scope không tự sử dụng facts trước đó.

## Debug và A/B/C cùng snapshot

```powershell
# Cùng input/config; forced mode chỉ dùng debug/evaluation
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question "Orion voltage?" --config configs/rag.local.json --mode baseline
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question "Orion voltage?" --config configs/rag.local.json --mode hierarchical
& ./.venv/Scripts/python.exe -m denso_a3 rag-ask --index .local/indexes/hierarchical_20261003 --question "Orion voltage?" --config configs/rag.local.json --mode auto
& ./.venv/Scripts/python.exe -m pytest tests/test_ui_auto.py tests/test_rag_interfaces.py -q
& ./.venv/Scripts/python.exe -m pytest -q
```

Multidoc evaluator --mode baseline/hierarchical/auto giữ mode; không ép hierarchical khi đo auto. Gold review_status=human_reviewed có questions/status/evidence_sets/calculations/acceptable_routes; phải review nguồn thực trước gán nhãn. Reports P50/P95/stage timings, initial/final route, agreement/escalation, calls/tokens/stop reasons và status/evidence/calculation checks. Substring and citation-pointer validity vẫn proxy; semantic answer và visual reading cần source review.

Một logical task retrieval chứa document/page/routed/escape searches nên calls không bằng HTTP/backend count. Deadline QueryBudget 300 giây/calls12/escalation1 được dùng xuyên mọi attempts; stage mới không reset budget. Model load tuần tự, cold regime; OS disk cache warm không là steady-state inference.

AppTest scripted demo widget/no BAT load/pending/cache/scope/citation UI khác demo model-real. Chưa có reviewed A/B/C toàn corpus hoặc SLA; kết quả mới, lỗi ban đầu/cách sửa và artifact thật được ghi trong [báo cáo](rag_architecture/UPDATE_IMPLEMENTATION_REPORT.md).
