# Báo cáo kiểm chứng câu trả lời và gợi ý metadata tự động

Ngày: 04/10/2026 (UTC+7). Dự án: DENSO A3.

## 1. Phạm vi và lựa chọn đã chốt

Triển khai hai tính năng: kiểm chứng câu trả lời trước khi gửi/lưu hội thoại, và tự trích metadata dưới dạng **gợi ý chờ xác nhận**. Người dùng đã chọn rõ: “Chỉ tạo bản gợi ý; mọi trường đều chờ người dùng xác nhận”. Vì vậy extractor không tự cập nhật giá trị registry, không tự gán `source_verified`, không tự thay đổi ranking/hard filters.

Chưa triển khai bộ phân loại chủ đề ngoài phạm vi. Scope vẫn là tài liệu và filters người dùng chọn. Metadata nhập tay qua form/JSON vẫn được giữ làm lựa chọn. Không thêm confidence theo phần trăm: kết quả kiểm chứng thể hiện mức hỗ trợ từ nguồn, không phải xác suất đúng đã hiệu chuẩn.

## 2. Trước thay đổi

BT7 yêu cầu claims có nguồn, kiểm tra citation ID và quote có thật; baseline cũng kiểm tra entity/attribute và coverage. Tuy nhiên, quote hợp lệ chưa chứng minh **ý nghĩa** của claim đúng. Một đáp án có thể giữ đúng số nhưng biến mục tiêu thành kết quả thực tế, gắn tên CEO vào đoạn doanh số hoặc đảo nghĩa phủ định. Claims và coverage còn được tạo cùng một lượt model.

Registry metadata được khởi tạo rỗng. Build chỉ ghép registry được cung cấp; muốn điền revision, thiết bị, ngày hiệu lực… người dùng cần chỉnh form/JSON. Bộ lọc chỉ tin metadata có trạng thái đã xác minh.

## 3. Luồng sau thay đổi

### 3.1. Kiểm chứng đáp án

```text
ask → scope/memory/planning → baseline hoặc hierarchical
    → bản nháp answered + evidence/citations
    → enforce_verification
        → gom mọi dòng có nội dung, gắn K ID do code cấp
        → ràng buộc từng ý với nguồn đã được trích dẫn trong scope
        → replay Decimal/renderer nếu là đáp án tính toán
        → một lượt local model kiểm chứng riêng
        → kiểm tra ledger đủ ID, không đổi nguồn và quote đúng nguyên văn
    → verified: giữ đáp án/citation
    → rejected/error: abstain, xóa citation của đáp án bị bác bỏ
    → một turn memory cuối cùng → đóng tài nguyên
```

Lượt kiểm chứng xem đúng đối tượng, thuộc tính, thời kỳ, đơn vị, phủ định, mục tiêu/kết quả, điều kiện và quan hệ nguyên nhân. Một dòng nhiều mệnh đề chỉ được supported khi **mọi phần** có căn cứ. `question_supported` còn kiểm tra bản nháp có giải quyết đầy đủ câu hỏi không. Việc thiếu ô ở cột/hàng không được hỏi không tự làm giá trị đã đọc rõ trở thành sai.

Code không để verifier tự thay nguồn: mỗi K ID có `allowed_source_ids`. JSON schema ràng buộc ID/quote theo từng ý; validator vẫn kiểm tra lại đủ/đúng ID và quote là span thật trong nguồn. Mọi dòng có nội dung của bản nháp được đưa vào kiểm tra, không tin danh sách claims do verifier tự rút ra. Các tiêu đề cố định của renderer tính toán được nhận diện riêng; bản tính toán phải khớp hoàn toàn renderer sau khi replay.

Caption ảnh chưa xác minh không là nguồn kiểm chứng. Observation ảnh phải có provenance của visual phase hiện tại và khớp document/page/image/chunk của evidence được truy xuất. Kiểm chứng ý nghĩa từ observation không chứng minh model đọc pixel đúng.

Bảng được truyền dưới dạng ngữ cảnh gọn: giữ text nguyên văn, header và các cell thuộc hàng có trong leaf, trạng thái ô và thông tin merged cells; không đưa payload native/bbox chi tiết của engine vào prompt. Citation gốc vẫn giữ bbox chuẩn hóa `[x0,y0,x1,y1]`, gốc trái trên; trang PDF bắt đầu từ 1, ảnh độc lập có page null.

### 3.2. Gợi ý metadata

```text
build/upload → extraction/chunking/caption
             → suggest_metadata (regex + local model)
             → metadata_suggestions.json riêng, mọi candidate unreviewed
             → embedding/index; registry vẫn giữ nguyên

snapshot cũ → rag-suggest-metadata → snapshot mới có gợi ý
            → chọn candidate + checkbox xác nhận từng trường
            → confirm_suggestions kiểm tra lại provenance
            → update_metadata tạo snapshot mới, chỉ trường đã chọn user_verified
```

Trường hỗ trợ: `document_family_id`, `revision_id`, `effective_from`, `effective_to`, `document_type`, `issuing_authority`, `document_language`, `equipment_id`, `part_number`.

Regex quét toàn bộ text chunk có nguồn, hỗ trợ nhãn Anh/Việt và các hàng bảng dạng field/value. Local LLM bổ sung candidate từ tối đa 32 text chunk/tài liệu; context được giảm theo tokenizer thật, có thông tin giới hạn trong artifact. Không lấy caption chưa xác minh làm metadata.

Mỗi candidate lưu value, origin, trạng thái, quote, document/source hash, page/chunk/image, bbox và text fingerprint. Giá trị định danh/revision/issuer phải xuất hiện trong quote; chỉ loại tài liệu/ngôn ngữ được phép là phân loại suy luận, có cảnh báo. Ngày hiệu lực tự trích chỉ chấp nhận ISO rõ ràng `YYYY-MM-DD`; ngày mơ hồ được ghi notes để nhập tay. Nhiều mã thiết bị/part giữ nhiều candidate, không tự ép một giá trị.

Xác nhận chỉ áp dụng trường người dùng chọn. Hệ thống kiểm tra lại schema, fingerprint registry, source hash, text và quote/frame; không ghi đè trường khác. Checkbox mặc định bỏ chọn và được tách trạng thái theo snapshot/tài liệu. Giá trị đã duyệt không bị extractor tự thay. Sau chỉnh/xác nhận, gợi ý của trường đã đổi được bỏ; gợi ý còn lại được giữ với fingerprint registry mới, nên có thể xác nhận tiếp mà không chạy lại model.

Upload gộp corpus có thể reuse gợi ý từ snapshot đi kèm `--registry`, nhưng chỉ khi source/text/config/model/registry và proof vẫn khớp. Source/config thay đổi sẽ trích lại. Nút Tạo lại gợi ý metadata chủ động chạy lại extraction.

Lượt extractor bị lỗi model không được cache để bỏ qua retry ở build sau; `model_status` và notes phân biệt completed, error, context_limited và not_requested.

## 4. File và API chính

| File | Thay đổi |
|---|---|
| `denso_a3/contracts.py` | Thêm `AnswerVerification`, `MetadataSuggestion`, `AnswerRecord.verification` và config liên quan |
| `denso_a3/bt7/verification.py` | Chuẩn bị claim/source, kiểm chứng ngữ nghĩa riêng, validate ledger và gate kết quả |
| `denso_a3/bt7/calculation_render.py` | Chuyển renderer tính toán sang BT7 để orchestration và verifier dùng chung |
| `denso_a3/pipeline/research_runner.py` | Import renderer BT7; giữ contract và output tính toán |
| `denso_a3/pipeline/rag_runner.py` | Kiểm chứng tại `finalize`, trước memory, áp dụng cho cả hai route |
| `denso_a3/pipeline/query_budget.py` | Counter verification riêng, cùng monotonic deadline và token accounting |
| `denso_a3/bt4/metadata_enrichment.py` | Trích gợi ý, cache kiểm tra nguồn, xác nhận candidate được chọn |
| `denso_a3/pipeline/index_builder.py` | Tự tạo artifact gợi ý khi build, backfill snapshot cũ, giữ gợi ý chưa xử lý sau cập nhật |
| `denso_a3/pipeline/rag_cli.py` | Lệnh `rag-suggest-metadata`, tùy chọn `--rules-only` |
| `denso_a3/ui/app.py` | Trạng thái/ledger kiểm chứng; tạo/xem/chọn/xác nhận gợi ý metadata |
| `configs/rag.local.json` | Bật hai tính năng và đặt giới hạn gọi model |
| `tests/test_answer_verification.py`, `tests/test_metadata_enrichment.py` | Kiểm tra gate, nguồn, budget, snapshot và chính sách chỉ-gợi-ý |
| `tests/test_pending_clarification.py`, `tests/test_dispatcher_review.py`, `tests/test_ui_auto.py` | Điều chỉnh fixture cho phase mới và kiểm tra UI xác nhận từng trường |
| `evaluation/demo_verification_metadata.py` | Demo trước/sau có fault injection, model local thật và tùy chọn tài liệu thật |
| `README.md` | Cập nhật call order, I/O, config, CLI và giới hạn |

Các API mới:

- `enforce_verification(answer, question, generator, config, budget, progress=None)` sửa AnswerRecord tại chỗ. Với answered, verified mới được giữ; rejected/error chuyển abstain trước khi memory ghi. Không tự mở rộng scope hoặc đổi route sau gate.
- `suggest_metadata(documents, chunks, registry, config, generator=None, previous=None)` trả artifact JSON riêng; không sửa input registry/chunks. Model lỗi vẫn giữ gợi ý regex, ghi notes. Unknown giữ unknown.
- `suggest_index_metadata(index_dir, output, config)` sao chép snapshot ready sang output mới và thêm gợi ý; không OCR/re-embed, không sửa registry nguồn.
- `confirm_suggestions(registry, suggestions, documents, chunks, document_id, selections)` nhận mapping `field → candidate index`, trả registry mới sau kiểm tra; chưa ghi file.
- `update_metadata(...)` tiếp tục cập nhật payload SQLite/Qdrant trong snapshot mới, không re-embed; gợi ý chưa xử lý được giữ khi chỉ registry thay đổi.

Config mặc định:

```json
{
  "verify_answers": true,
  "verification_max_calls": 2,
  "auto_metadata_suggestions": true,
  "metadata_use_llm": true,
  "metadata_max_calls_per_document": 2
}
```

`verification_max_calls=2` là một lần kiểm chứng và tối đa một lần sửa JSON không hợp lệ. Một kết luận ngữ nghĩa bác bỏ hợp lệ không được chạy lại để tìm model đồng ý. Chưa tự viết lại đáp án bị bác bỏ; hiện từ chối và hiển thị lý do. Lỗi runtime, context, deadline hoặc output sai cấu trúc đều không cho answered đi qua.

`verify_answers=false` là debug/A-B, output ghi disabled; không phải trạng thái đã kiểm chứng. `metadata_use_llm=false` dùng regex. `auto_metadata_suggestions=false` bỏ bước gợi ý khi build. Không tự tải model và không chuyển cloud.

## 5. Kịch bản demo trước và sau

### 5.1. Tái hiện lỗi bằng bản nháp được chủ động đưa vào

Để so sánh ổn định, script chủ động đưa bản nháp vào **lượt viết đáp án**, giữ nguyên validation citation/quote/coverage. Phase before đặt `verify_answers=false` để tái hiện cơ chế cấu trúc trước thay đổi; phase after bật verifier và gọi **model local thật**. Đây không phải chạy một checkout lịch sử, cũng không chứng minh model cũ tự nhiên luôn tạo ra những lỗi này.

| Ca | Nguồn và bản nháp | Before | After |
|---|---|---|---|
| `valid_voltage` | Nguồn `M1 rated voltage: 24 V`; đáp án 24 V | answered | answered + verified |
| `target_as_actual` | Nguồn là mục tiêu BAT 2025 GBP120; bản nháp nói doanh số thực tế GBP120 | answered, quote/citation hợp lệ | abstain; claim bị bác bỏ |
| `unrelated_ceo` | Nguồn là doanh số BAT; bản nháp nói CEO là Alice | answered, nguồn đúng tên BAT nhưng không chứng minh CEO | abstain; thiếu bằng chứng |
| `reversed_negation` | Nguồn nói M1 không cần calibration; bản nháp nói cần | answered | abstain; phủ định bị đảo |

Sau bị bác bỏ, câu trả lời cuối không còn nội dung sai và citation giả hỗ trợ; evidence/ledger vẫn có để debug. Lượt bị bác bỏ không được biến thành trả lời thành công trong memory.

### 5.2. Lượt chạy hoàn toàn bằng model

`natural_voltage` sử dụng model local thật cho cả viết đáp án và kiểm chứng; giữ đáp án điện áp 24 V. `natural_calculation` dùng hierarchical, lập kế hoạch/tìm dữ kiện Alpha GBP100 và Beta GBP200, Decimal tính tổng GBP300, kiểm chứng đầu vào và công thức, giữ answered + verified.

Có thêm smoke test trên snapshot thật `.local/indexes/hierarchical_20261003`, config hybrid/reranker/visual production:

- `corpus_budget`: hỏi Latest Estimate cost của Jessie, nguồn `nhwc0428.pdf`, trang 11, kind table. Giá trị cần đối chiếu là GBP469,000. Ô Variance chưa đọc được không phải nội dung được hỏi.
- `corpus_visual`: đọc tên đại học từ `images/24580.png`; đáp án phải dựa vào visual observation có provenance, qua verifier trước khi hiển thị.

Các ca này là smoke checks đối chiếu nguồn đã có, không phải benchmark human-reviewed toàn corpus.

### 5.3. Metadata theo đúng lựa chọn của người dùng

Nguồn demo có Document number SOP-001, Revision B, Equipment ID M1 và các trường khác.

| Bước demo | Before | After |
|---|---|---|
| Mở metadata của tài liệu mới | Registry rỗng, cần nhập tay | Có gợi ý kèm quote/trang, registry vẫn rỗng |
| Chưa xác nhận, lọc revision B | Không có metadata đã duyệt | Vẫn không có kết quả; gợi ý chưa thành authority |
| Chỉ xác nhận revision B và equipment M1 | Người dùng cần điền các trường | Chỉ hai candidate được chọn thành user_verified |
| Lọc revision B sau xác nhận | Cần metadata nhập tay hợp lệ | Tìm được chunk metadata của đúng tài liệu |
| Các trường không chọn | Rỗng hoặc giá trị hiện có | Giữ nguyên, chưa được tự áp dụng |

Trong script demo, thao tác xác nhận được mô phỏng bằng lời gọi `confirm_suggestions` với hai trường trên, chỉ trên snapshot synthetic mới. AppTest kiểm tra checkbox ban đầu bỏ chọn, không chọn thì không lưu, chỉ chọn revision thì equipment vẫn chưa được áp dụng. Không tự xác nhận metadata của kho tài liệu thật.

## 6. Kiểm chứng và kết quả thực chạy

Regression mới bao gồm: quote có thật nhưng claim sai ngữ nghĩa; thiếu yêu cầu câu hỏi; ledger thiếu/trùng ID; nguồn bị đổi; quote giả; câu không có citation; caption/observation sai nguồn; deadline/context/runtime; tính toán replay/renderer; một memory turn sau gate; metadata chỉ-gợi-ý; hash/text/registry stale; ngày mơ hồ; nhiều thiết bị; cache; build/backfill và xác nhận trong UI.

Các unit/AppTest dùng scripted model để kiểm tra contract và luồng, không dùng làm số đo năng lực ngữ nghĩa của model.

Artifact thực chạy:

- `results/verification_metadata_demo_20261004`: lần đầu 10/11 checks; một phép tính hợp lệ bị lỗi quote của verifier.
- `results/verification_metadata_calc_recheck_20261004`: phép tính đạt sau khi cung cấp source tính toán dễ trích nguyên văn và ràng buộc quote/source theo claim.
- `results/verification_metadata_demo_final_20261004`: 11/11 checks của demo tổng hợp; gồm 8 lượt before/after, 2 lượt natural và 1 kiểm tra metadata.
- `results/verification_metadata_production_20261004`: bản đầu trên tài liệu thật bộc lộ payload bảng quá lớn, verifier hết context; không cho qua answered.
- `results/verification_metadata_production_final_20261004`: sau rút gọn payload bảng, visual đạt; table bị verifier đòi thêm cột Variance không nằm trong câu hỏi.
- `results/verification_metadata_table_recheck_20261004`: table đạt sau bổ sung requirements và quy tắc chỉ xét trường được hỏi. Giá trị GBP469,000 được giữ; verification khoảng 5,68 giây, tổng khoảng 24,37 giây ở lượt này, gồm load model/retrieval.

Giữ nguyên các artifact thất bại ban đầu để phân biệt lỗi đã phát hiện/sửa với kết quả cuối.

### 6.1. Kết quả chấp nhận cuối

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| Toàn bộ pytest | **208 passed**, khoảng 32 giây | `python -m pytest -q`; gồm regression cũ, verifier, metadata và AppTest |
| Metadata sau bổ sung kiểm tra candidate index | **14 passed** | `python -m pytest tests/test_metadata_enrichment.py -q` |
| Demo tổng hợp | **11/11 checks** | `results/verification_metadata_acceptance_20261004/summary.json` |
| Smoke test tài liệu thật, config production | **2/2 checks** | `results/verification_metadata_source_acceptance_20261004/summary.json` |
| CLI mới | Help/parser chạy được | `python -m denso_a3 rag-suggest-metadata --help` |
| Diff của file tracked | Không có lỗi whitespace | `git diff --check` (chỉ cảnh báo chuẩn hóa CRLF của README) |

Trong lượt chấp nhận tổng hợp cuối, ba bản nháp sai vẫn bị từ chối; điện áp và phép tính đúng vẫn verified. Cặp before/after của ca có chủ động đưa bản nháp vào mất khoảng 5,04–5,08 giây trước và 6,52–7,49 giây sau. Lượt natural voltage khoảng 8,47 giây; natural calculation khoảng 24,30 giây. Đây là nguồn nhỏ synthetic và các lượt load tuần tự, không phải độ trễ steady-state hay benchmark toàn corpus.

Trên nguồn thật, Jessie được trả lời **GBP469,000**, tổng khoảng 25,12 giây, phase verification khoảng 5,73 giây. Tên đại học được trả lời **UNIVERSITY OF CALIFORNIA, SAN DIEGO**, tổng khoảng 15,73 giây, verification khoảng 1,84 giây. Các checks xác nhận status/verification; không diễn giải 2/2 thành độ chính xác 100% của hệ thống.

Artifact `metadata_demo.json` của lượt chấp nhận xác nhận draft có trusted fields `{}`, sau thao tác xác nhận mô phỏng chỉ có `revision_id=B`, `equipment_id=M1`; hard filter revision B đổi từ không có chunk sang đúng chunk `metadata` của fixture. Gợi ý không chọn vẫn chưa được áp dụng.

Model/runtime: Qwen3-VL-2B-Instruct Q4_K_M, llama.cpp b11235 trong cache có sẵn; production dùng BGE-M3/reranker local đã cấu hình. Demo synthetic dùng lexical và vectors fixture để tập trung vào verification; không dùng để kết luận hybrid tốt hơn hoặc đo độ chính xác retrieval.

## 7. Cách demo lại

Từ gốc dự án, dùng output mới:

```powershell
& ./.venv/Scripts/python.exe -m pytest -q
& ./.venv/Scripts/python.exe evaluation/demo_verification_metadata.py --output results/my_verification_metadata_demo
& ./.venv/Scripts/python.exe evaluation/demo_verification_metadata.py --production --output results/my_verification_source_demo
& ./.venv/Scripts/python.exe -m denso_a3 rag-suggest-metadata --index .local/indexes/hierarchical_20261003 --output .local/indexes/my_metadata_drafts --config configs/rag.local.json
# Nếu chỉ muốn regex và không chạy model:
& ./.venv/Scripts/python.exe -m denso_a3 rag-suggest-metadata --index .local/indexes/hierarchical_20261003 --output .local/indexes/my_metadata_rules --config configs/rag.local.json --rules-only
```

Demo UI: mở `rag-ui` → chọn snapshot → tab Hỏi đáp xem trạng thái và “Kết quả kiểm chứng từng ý”. Tab Metadata → Tạo lại gợi ý metadata (nếu snapshot cũ chưa có) → mở snapshot mới → chọn candidate → đánh dấu đúng trường muốn xác nhận → Xác nhận các gợi ý đã chọn → mở snapshot được tạo → thử filter revision/equipment đã duyệt. Form/JSON chỉnh tay vẫn hoạt động.

## 8. Giới hạn thực tế

- Verifier là một lượt riêng nhưng dùng cùng model local; không phải một nguồn chân lý độc lập. Vẫn có thể chấp nhận nhầm hoặc từ chối nhầm.
- Source support không bảo đảm nguồn đúng, OCR đúng hoặc model đọc pixel đúng. Quote tồn tại và schema hợp lệ cũng chưa đủ để gọi confidence 99%.
- Tăng latency/tokens do thêm lượt kiểm chứng. Deadline/context không được reset hoặc bỏ gate để trả lời nhanh hơn.
- Metadata LLM đọc context giới hạn; regex quét toàn text nhưng chỉ hiểu nhãn đã hỗ trợ. Không cam kết tìm hết metadata của tài liệu dài hoặc chỉ có thông tin trong ảnh chưa OCR được.
- Mọi metadata gợi ý đều chờ người dùng xác nhận. Một tài liệu nhiều thiết bị có nhiều candidate nhưng registry hiện giữ một string/trường; có thể để trống hoặc nhập tay, chưa thêm schema đa giá trị/cấp trang.
- Legacy registry validator vẫn chấp nhận nhãn source_verified do người dùng nhập; nó chưa kiểm chứng ngữ nghĩa nhãn này. Extractor mới không dùng nhãn đó để tự tạo authority.
- Chưa thêm bộ xác định miền câu hỏi và chưa có tập holdout human-reviewed đủ để đo tỷ lệ trả lời sai/từ chối nhầm toàn hệ thống.
