# Hierarchical research trong hệ thống tự định tuyến

Ngày đối chiếu code: 03/10/2026. [Báo cáo implementation/demo](UPDATE_IMPLEMENTATION_REPORT.md) ghi kiểm chứng mới; [CHANGE_REPORT](../CHANGE_REPORT.md) giữ kết quả lịch sử trước update. Ví dụ ở đây mô tả contract, không là benchmark mới.

Hierarchical là runner lập tasks/dependencies, tìm document → page → leaf cộng escape trong cùng scope, validate facts, kiểm tra đủ inputs rồi Decimal calculation hoặc quoted-fact synthesis. Auto router chọn runner khi cần nhiều bước/phép tính/so sánh, hoặc escalation từ baseline thiếu support. UI không chọn mode; forced CLI mode phục vụ debug/A-B.

## Hierarchy và snapshot

```text
Document node (một nguồn)
 └─ Page node (trang vật lý)
     └─ Leaf text/table/figure/page_visual chunk
```

`bt4.hierarchy.build_hierarchy` tạo synopsis extractive có chunk/page IDs, document/parent IDs và origin. Node dùng định tuyến, không là fact/citation. Tầng giữa là page vật lý, chưa gom mục xuyên trang. Graph validator kiểm tra duplicate/orphan/source mismatch/leaf coverage.

| Artifact | Nội dung |
|---|---|
| records.sqlite / vectors collection chunks | Leaf text/FTS/vector/payload, giữ compatibility baseline |
| hierarchy.json / hierarchy.sqlite | Nodes/parent-child graph và routing records |
| routing_nodes | Embeddings document/page synopsis |
| registry.json / documents.json | Nguồn và verified metadata |
| parents.json / assets / sources | Table context và nguồn/canonical page images |
| manifest.json | Ready/schema 1.1/hierarchy_version/model config |

`rag-upgrade` tạo output mới, giữ source snapshot, không OCR lại/re-embed leaf. Không sửa manifest cũ để giả hierarchy. Legacy schema 1.0 vẫn chạy direct baseline; research trên index thiếu hierarchy trả capability_missing trước task execution. Query không tự build/download.

## Dispatcher, planner và plan reuse

```text
ask → config/local policy → ready/scope/pending/memory
    → plan_query + current-scope intersection + analyze_query
    → route_question rules hoặc local planner bounded
    → run_research(research_plan=validated_plan hoặc None, budget=cùng request)
```

Rules là model-free cho tín hiệu rõ. Ambiguous dùng local LLM tạo bounded plan, validator chạy trước route execution. Nếu router đã có valid research plan, runner reuse; không lập lại plan. Forced hierarchical đi thẳng vào runner rồi planner khi chưa có plan. Hierarchy/research context capability được kiểm tra trước execution; không cắt required evidence âm thầm để vừa config nhỏ.

`run_research(root, question, query_plan, config, manifest, generator, research_plan=None, question_conditions=None, budget=None, progress=None, clarification=None)` trả AnswerRecord. Nó không sở hữu UI pending/assumptions logic hoặc ghi memory. Dispatcher giữ original question + literal clarification và finalization.

Plan schema có tasks, calculations, clarification và question_conditions. Tasks T IDs có query/entity/kind/expected_unit/depends_on; query `{T1}` chỉ chạy khi T1 có validated fact. C IDs chỉ sum/subtract/multiply/divide, inputs là valid T/C/U IDs. Validator kiểm tra unique IDs, dependency DAG, typed operands và literal condition provenance; numeric literal tùy ý không được calculator dùng.

Grammar gán direct voltage/pressure lookup có entity thành quantity, kể cả câu tiếng Việt; không biến definition hoặc manufacturer bridge thành numeric task. Arithmetic cần calculator operations hoặc clarification thực sự. Reused plan cũng được kiểm lại yêu cầu phép tính, entity/operands và condition spans. Unit aliases m/cm/mm, kPa/MPa/Pa giữ normalized dimensions; unsupported unit bị từ chối.

## Điều kiện người dùng khác document facts

Kênh assumptions JSON đã bỏ xuyên UI/CLI/config/planner/runner/evaluator. Missing business relation không tự gán 0/1/100%. Planner phải tạo sourcing task, clarification hoặc abstain.

QuestionCondition U IDs chỉ từ literal question/clarification, origin user_question/user_clarification, raw_text + start/end, applies_to và value/unit/dimensions khi parse được. Code resolve unique spans; literal lặp/unsupported numeric/meaning chưa rõ bị từ chối. Percent explicit normalize chia 100 có trace, giữ wording và entity/relationship.

BAT “hoàn thành 100% mục tiêu giảm illicit volume” không tự là “100% lượng giảm chuyển thành BAT sales”. Fixture mới tách câu thiếu transfer và câu nói rõ transfer. Source facts giữ chunk IDs/citation; scenario ratio giữ condition_ids không fake source. Calculator chỉ dùng U rows khớp validated plan và revalidate provenance trước tính.

Với câu tính margin từ illicit reduction chưa nói quan hệ transfer, planner có thể trả clarification bằng rules trước model I/O. Câu yêu cầu tìm sourced transfer factor vẫn dùng task tìm nguồn. Pending do dispatcher giữ original question cùng literal clarification tích lũy; search_question đã resolve tên chỉ là context tìm kiếm, không là nguồn condition.

## Retrieval từng task và escape

Tasks đủ dependencies được tìm theo vòng, với cùng immutable QueryPlan hard filters. `retrieve_hierarchical`:

1. Tính allowed leaf IDs trong explicit source/document/page/kind/verified metadata scope.
2. Encode query vector một lần khi strategy cần semantic, đóng encoder.
3. Rank document nodes trong allowed scope, rồi page nodes của documents được chọn.
4. Retrieve leaf ở routed pages qua keyword/dense, RRF và rerank như baseline.
5. Retrieve leaf escape trên toàn allowed scope, reuse vector của task.
6. Nối routed trước escape, deduplicate chunk IDs và mở rộng table leaves thật trong scope.

Escape hiện chạy trong mỗi task retrieval; không chỉ khi routing thất bại. Hai lists sau nối không được rerank chung toàn bộ lần nữa. Một logical retrieval_call là một task query, bên trong có nhiều document/page/backend/routed/escape operations. Trace ghi danh sách queries, routing/escape counts/timings; không quảng bá hierarchy luôn nhanh hơn baseline.

Task sau dependency dùng fact.value/entity validated để thay placeholder. Nếu manufacturer chưa tìm được, không đoán tên nhà sản xuất để chạy budget query. Missing-query bổ sung vẫn giữ entity/unit/scope; used_queries ngăn lặp vô ích.

## Shared VLM và text/visual facts

Sau retrieval, mỗi task gọi cùng `run_visual_phase` với task requirements. Phase chỉ chạy khi visual policy/intent/hits hoặc OCR/table thiếu yêu cầu; text đủ thì skip. Retrieval models đóng trước generation.

Code chọn source assets SHA thật, allowed crops và IDs; tối đa hai images/call, tổng visual calls tính repairs/cache. Crop bbox giữ normalized canonical source frame; partial-page scope không gửi pixels ngoài phạm vi. Model trả observed/unreadable/ambiguous với literal value/unit và visible entity/row/column labels. Unknown IDs, sai entity/cột/unit/raw literal bị reject; unreadable/ambiguous không thành usable fact. Caption chưa phase không là fact.

`extract_fact` có source rules rồi LLM fallback text; quote/value/entity phải nằm trong real text/table source. Latest Estimate cần đúng row + column, không lấy Co plan hoặc tiền tệ khác. `source_visual` fact chỉ từ validated observation và parse/unit checks, có support_kind=image, observation/image IDs, support_text và visual provenance. Không giả quote OCR substring cho image fact.

Image/visual/OCR disagreement cùng field có conflict trace. Cache request-local chứa task/requirements/query/model/source/scope; không reuse kết quả sai task/scope. Observation validation xác lập provenance/format, không chứng minh pixel reading đúng.

## Coverage, conflict và giới hạn tìm tiếp

`check_coverage(plan, facts)` chỉ ready khi tasks có inputs đúng plan. Missing tasks được tìm thêm khi có query mới và budget. `fact_conflicts` so sánh candidate values cùng task, giữ numeric/version conflict gate theo verified metadata. Conflict giữ sources; không chọn nguồn thuận lợi hoặc quay baseline.

Mặc định 6 tasks, 3 rounds, 12 stage task queries, 6 routed documents/4 pages và 5 evidence/task. Tổng request QueryBudget còn giới hạn 12 logical calls kể cả baseline probe, 300 giây, 1 escalation, 2 router attempts và 6 visual calls gồm repair. Stage deadline bằng min(research ceiling, query remaining); không reset khi escalation. Context research mặc định 4.500 tokens và phải để chỗ prompt/answer. Context thiếu trả lỗi/budget outcome, không cắt đầu vào để giả complete.

Stop reasons gồm coverage_complete, no_progress, deadline/query_deadline, call budget, round_budget, conflict, context_budget, user_choice_required, capability_missing. Coverage đủ không phải confidence đã hiệu chỉnh hoặc chứng minh tasks model lập đúng ý người hỏi.

## Tính toán hoặc synthesis

`calculate(operations, facts, question_conditions=None, validated_plan=None)` dùng Decimal, normalized dimensions/currency và division checks. Sum/subtract khác unit/currency hoặc explicit source periods không compatible bị từ chối; không `eval`. Result có operation/inputs/value/dimensions, union document sources và condition_ids riêng. Renderer hiện source citations từng input, công thức/kết quả và phần điều kiện người dùng riêng.

Không arithmetic thì `grounded_answer → grounded_fact_answer` nhận text quotes/visual support_text đã validate. Model trả claims/source IDs, code render inline citations; mỗi required source phải xuất hiện. Repair tối đa một lần; không ảnh mới trong final answering. Fact image phải được renderer/citation giữ provenance đúng loại support.

## Finalization, UI và kiểm chứng

Dispatcher giữ attempts và routing initial/final/method/reason/capability/escalations, budget/tokens/timings và final trace. Nó chỉ append một long-term turn; instant pending ở RAM, long-term pending/turns SQLite. Pending schema 2.0 kiểm tra scope/content/config, legacy assumptions không được tiếp tục. History chỉ context tham chiếu.

UI giữ scope/memory/advanced filters, source images và trace; không đọc file BAT hoặc selector mode/strategy. Corpus/config/scope đổi invalidates result/pending. Forced baseline/hierarchical/auto dùng cùng snapshot/model/scope cho A/B/C evaluator; labels không vào inference.

Tests scripted xác nhận graph/dependency/scope/unit/visual/pending/budget behavior. Demo model local thật và source review là bằng chứng riêng trong [báo cáo](UPDATE_IMPLEMENTATION_REPORT.md). Chưa có gold human-reviewed toàn corpus đủ để kết luận speed/accuracy hơn baseline; quote, citation pointer, coverage và route agreement không tự chứng minh final semantic correctness.
