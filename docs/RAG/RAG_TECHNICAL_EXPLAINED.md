# Giải thích đầy đủ ý tưởng kỹ thuật RAG

Tài liệu dành cho người muốn hiểu hệ thống trước khi đọc Python. Code cụ thể ở [báo cáo kiến trúc](RAG_ARCHITECTURE_IMPLEMENTATION.md); thao tác ở [hướng dẫn demo](RAG_DEMO_GUIDE.md).

## 1. Bài toán thực sự cần giải quyết

Người dùng có nhiều PDF và ảnh: chữ in, bảng thông số, biểu đồ, chữ viết tay; hỏi bằng tiếng Anh hoặc Việt. Hệ thống cần tìm đúng tài liệu, hiểu đúng hàng/cột, phân biệt phiên bản, trả lời và chỉ ra nơi có bằng chứng. Một câu trả lời trôi chảy nhưng lấy sai thiết bị hoặc nhầm revision vẫn là sai.

RAG là **tìm tài liệu trước, sinh câu trả lời sau**. Model ngôn ngữ không được huấn luyện lại từ những file upload trong prototype. File được chuyển thành một kho tìm kiếm; mỗi câu hỏi lấy một phần nhỏ liên quan đưa vào model. Thêm tài liệu nghĩa là tạo/cập nhật kho tìm kiếm, không phải fine-tune model.

Ví dụ người dùng hỏi “Áp suất tối đa của PUMP-01 là bao nhiêu?”. Hệ thống tìm các đoạn có PUMP-01 và áp suất, giữ nguồn/trang, kiểm tra khác biệt giữa phiên bản rồi mới viết đáp án. Nếu hai bản ghi 5 MPa và 6 MPa mà chưa rõ bản có hiệu lực, hệ thống cần dừng ở cảnh báo.

## 2. Vì sao BT1–BT3 vẫn quyết định chất lượng

BT1 biến PDF/ảnh thành các trang canonical có định danh và hash. PDF có trang vật lý 1, 2, 3…; ảnh rời không tự gán là trang 1. Hash mô tả nội dung byte: đổi file thì hash đổi. Hash giúp phát hiện alias giống nhau và tránh sử dụng metadata của phiên bản cũ.

BT2 OCR đọc chữ từ pixel. BT3 layout xác định đoạn, tiêu đề, hình, bảng và thứ tự đọc. TableFormer giúp nhận cấu trúc hàng/cột; OCR điền chữ vào ô. Hai việc này khác nhau: đọc đúng “5” mà đặt vào hàng sai vẫn cho câu trả lời sai.

Một ô trống trong kết quả OCR không luôn có nghĩa ô gốc trống. Nó có thể là chữ mờ, ô gộp hoặc mất nội dung. Vì vậy code ghi unknown/partial và giữ ảnh gốc. Tọa độ bbox normalized từ 0 đến 1 giúp biết chữ nằm ở đâu dù kích thước ảnh thay đổi.

OCR Việt đã có đường chạy trên GPU, nhưng thử nghiệm hiện có cho thấy một số dấu bị đọc sai. Đây là lý do phải giữ nguồn ảnh và đo chất lượng theo ngôn ngữ; cờ `status=ok` chỉ nói tác vụ chạy được, không nói mọi ký tự đúng.

## 3. Chunk: cắt tài liệu để tìm và đọc được

Model không thể nhận vô hạn trang trong mỗi câu hỏi. Hệ thống chia tài liệu thành **chunk**, tức các đơn vị văn bản đủ nhỏ để tìm kiếm và đủ ngữ cảnh để hiểu.

- Prose: gom theo câu/đoạn và tiêu đề; mặc định khoảng 384 token.
- Overlap 48 token: giữ phần cuối trước ở phần sau để giảm mất liên kết. Không phải mỗi chunk luôn trùng chính xác 48 token; code ưu tiên ranh giới câu.
- Bảng: nhóm hàng kèm header, giữ chỉ số cột, ô gộp và ô unknown. Nếu cắt bảng như văn bản thuần, số có thể mất tên thuộc tính hoặc đơn vị.
- Visual record: mô tả một ảnh/trang giúp tìm các nội dung không có OCR tốt.
- Parent: liên kết chunk về bảng hoặc trang đầy đủ. Với bảng nhỏ, khi trả lời có thể đưa cả bảng vào context.

Token là đơn vị model xử lý, không đồng nhất với từ hoặc ký tự. Tiếng Việt có thể cần nhiều token hơn cùng số từ tiếng Anh. Một hàng bảng quá dài không bị bỏ âm thầm: embedding chia thành cửa sổ và tổng hợp vector; chất lượng của phép tổng hợp này vẫn cần benchmark.

## 4. Embedding và dense retrieval

Embedding biến một đoạn hoặc câu hỏi thành danh sách số, gọi là vector. BGE-M3 trong hệ thống tạo vector biểu diễn nội dung đa ngôn ngữ. Câu hỏi tiếng Việt và đoạn tiếng Anh có ý nghĩa gần nhau có thể nằm gần nhau trong không gian vector.

Cosine similarity đo hướng của hai vector. Điểm cao nghĩa là giống theo model; không phải xác suất đáp án đúng. Dense retrieval phù hợp với paraphrase, nhưng đôi khi coi hai mã gần nhau là tương tự. “PUMP-01” và “PUMP-02” phải được phân biệt bằng metadata/từ khóa và bằng chứng cụ thể.

Qdrant giữ các vector và tìm láng giềng gần nhất. Prototype dùng Qdrant local trên đĩa; chưa cần dịch vụ riêng. Embedding không chứa quyền truy cập hoặc xác nhận revision: metadata phải đi kèm.

Code hỗ trợ BGE và nhánh E5 tùy chọn. BGE dùng vector CLS đã chuẩn hóa; E5 dùng trung bình token có mask, với `query:` cho câu hỏi và `passage:` cho tài liệu. Không được dùng vector từ hai model khác nhau trong cùng index, dù số chiều vô tình bằng nhau.

## 5. Lexical, hybrid, fusion và reranker

Lexical tìm những từ thực sự xuất hiện trong tài liệu. SQLite FTS5 cung cấp BM25: xét từ khớp, độ hiếm của từ và chiều dài đoạn. Nó nhanh, tốt cho mã và tên, nhưng không tự hiểu bản dịch.

Hybrid chạy cả lexical và dense. Vì hai điểm số khác thang đo, không cộng trực tiếp cosine và BM25. Code dùng **Reciprocal Rank Fusion**: mỗi nhánh đóng góp `1/(60 + thứ hạng)`. Đoạn được cả hai nhánh xếp cao có lợi thế.

Reranker đọc câu hỏi cùng từng candidate để đánh giá sự liên quan sát hơn. Nó tốn hơn embedding vì phải xử lý từng cặp câu hỏi–đoạn; bởi vậy chỉ áp dụng sau khi lấy một số candidate, không đọc toàn bộ kho. Mặc định lấy candidate mỗi nhánh, giới hạn 12 cho reranker và 5 evidence cuối. Điểm reranker cũng không phải chứng nhận đáp án đúng.

Do các bước dùng chung contract, nhiều kiến trúc có thể cùng tồn tại. A/B/C/D là lựa chọn đường chạy trong một hệ thống, không cần bốn ứng dụng riêng. Auto dùng luật nhận diện câu hỏi và loại evidence; Advanced cho người dùng ép đường chạy để so sánh. Hiện các nhánh chạy tuần tự trên máy một GPU, không có bốn model suy luận đồng thời.

## 6. Metadata-aware retrieval là gì?

Metadata là thông tin **về** tài liệu: thiết bị, part number, loại tài liệu, revision, cơ quan phát hành, ngày hiệu lực, ngôn ngữ. Nội dung là thông số/quy trình **trong** tài liệu.

Có hai cách dùng metadata:

1. **Hard filter:** điều kiện bắt buộc. Người dùng chọn file A thì file B không được xuất hiện dù điểm semantic cao. “Revision R2” chỉ dùng như filter khi R2 đã được xác minh.
2. **Soft preference:** tăng ưu tiên. Nếu hệ thống đoán câu hỏi liên quan một loại tài liệu thì có thể cộng điểm, nhưng vẫn giữ tài liệu thiếu nhãn để tránh bỏ sót.

Mỗi giá trị đi kèm nguồn gốc, trạng thái kiểm duyệt và bằng chứng xác minh. `null` nghĩa là chưa biết. Tên file chứa “new” hoặc thời gian sửa file gần đây không chứng minh phiên bản có hiệu lực.

Điểm quan trọng là **lọc trước top-k ở cả lexical và dense**. Nếu tìm toàn kho trước rồi mới bỏ các nguồn không hợp lệ, top 5 có thể đều bị loại dù nguồn đúng có trong kho. Code xây cùng tập eligible cho cả SQLite và Qdrant để tránh sai khác giữa hai nhánh.

Metadata-aware phù hợp nhất với hybrid làm nền vì vừa tận dụng mã chính xác vừa tìm nghĩa. Tuy nhiên nó cũng áp dụng cho A, B và D. Nó là lớp kiểm soát phạm vi/ưu tiên xuyên suốt, không phải đối thủ của hybrid hay vision.

## 7. Ảnh, biểu đồ và chữ viết tay

OCR văn bản không biểu diễn tốt trục biểu đồ, đường cong hoặc hình học. Nhánh visual có hai thời điểm dùng VLM:

- Khi build: tạo mô tả ảnh để index tìm được candidate.
- Khi hỏi: đưa ảnh thật của candidate vào model để trả lời và đối chiếu số.

Mô tả ảnh là dữ liệu sinh tự động, có thể sai. Hệ thống ghi rõ “unverified”; nếu chỉ tin caption mà không xem ảnh thì lỗi caption sẽ trở thành đáp án. Hiện một câu nhận tối đa hai ảnh đã thu nhỏ; chữ nhỏ trên trang lớn vẫn có thể không đọc được. Qwen 2B là lựa chọn vừa phần cứng, không đảm bảo đọc chính xác mọi bảng hay chữ viết tay.

Auto bật vision khi câu hỏi có dấu hiệu hình/biểu đồ hoặc visual record/figure xếp cao. Người dùng có thể ép D khi Auto chưa nhận ra. Không đủ độ rõ phải abstain; không đoán số chỉ vì model có thể viết một con số hợp lý.

## 8. Phiên bản và mâu thuẫn

So sánh 5 MPa và 5000 kPa cần đổi cùng đơn vị trước; chúng không mâu thuẫn. So sánh áp suất của hai máy khác nhau cũng không phải conflict. Gate yêu cầu cùng họ tài liệu, cùng thiết bị/part và cùng thuộc tính.

Code hiện phát hiện một lớp hẹp: dòng có nhãn, số và đơn vị. Nó có thể cảnh báo 5 MPa và 6 MPa ở hai nguồn liên quan. Nó chưa hiểu đầy đủ mâu thuẫn quy trình như “bật van trước” và “bật van sau”, hoặc mọi cấu trúc bảng. Synthetic tests kiểm tra logic này; không được gọi là độ chính xác trên tài liệu DENSO thật.

Ngày hiệu lực đã xác minh cho phép giới hạn tài liệu đang có hiệu lực. Nếu metadata thiếu, hệ thống hỏi lại/không kết luận thay vì tự chọn file mới nhất. Nếu người dùng chỉ chọn một nguồn, hệ thống giữ scope đó; không bảo đảm phát hiện mâu thuẫn với nguồn đã bị loại khỏi phạm vi.

## 9. LLM, grounding và citation

LLM nhận câu hỏi, evidence có ID như E1/E2, cờ chất lượng và ảnh nếu cần. Prompt yêu cầu chỉ dùng bằng chứng, giữ số/đơn vị, trả lời theo ngôn ngữ câu hỏi và từ chối khi thiếu dữ liệu.

Tài liệu có thể chứa câu dạng “hãy bỏ qua mọi hướng dẫn”. Với hệ thống, đó vẫn là chữ trong tài liệu, không phải yêu cầu của người dùng. Prompt phân biệt dữ liệu với chỉ thị; tuy vậy prompt đơn lẻ không là bằng chứng chống mọi prompt injection.

Model trả JSON; code kiểm tra ID dẫn nguồn có nằm trong evidence được cấp, ảnh còn tồn tại và số trang hợp lệ. Citation bịa bị báo lỗi. Cần phân biệt:

Với model local, JSON schema còn giới hạn trực tiếp các ID có thể sinh. Nếu hậu kiểm vẫn thất bại, hệ thống yêu cầu sinh lại tối đa một lần, không tự gán citation khác để biến đáp án lỗi thành hợp lệ. Schema chỉ bảo đảm hình thức và tập ID, không chứng minh claim đúng.

- **Citation validity:** đường dẫn/trang tồn tại và thuộc evidence.
- **Citation correctness:** nguồn đó thật sự chứng minh claim.

Prototype kiểm tra tự động phần đầu. Phần sau cần đọc nguồn, nhãn đã soát hoặc một bộ đánh giá đáng tin. Câu “có citation” không đồng nghĩa “đúng nghiệp vụ”.

## 10. Trí nhớ hội thoại khác kho tài liệu

Kho RAG lưu tài liệu và index. Kho hội thoại lưu những gì người dùng đã hỏi và đáp án trước. Chúng không được trộn thành cùng một nguồn sự thật.

Mode tức thì: mỗi câu độc lập, không mở SQLite hội thoại, không ghi câu hỏi xuống đĩa mặc định. UI chỉ giữ đáp án hiện tại trong bộ nhớ phiên. Upload và index vẫn được lưu vì người dùng yêu cầu thêm tài liệu; CLI `--output` và benchmark ghi kết quả theo yêu cầu.

Mode dài hạn: mỗi cuộc trò chuyện có ID riêng; đóng/mở ứng dụng vẫn đọc lại được. Câu hỏi nối tiếp được viết lại để giải tham chiếu, sau đó truy xuất mới. Context chỉ dùng cùng index/phạm vi; đổi nguồn không lôi câu trả lời ở nguồn cũ vào. Context đưa model được giới hạn, còn lịch sử đã lưu vẫn tồn tại trên đĩa. Đây là persistent conversation, chưa phải hệ thống tự học hồ sơ người dùng hoặc nhớ mọi thông tin vô hạn.

## 11. Vì sao snapshot, hash và manifest quan trọng

Một benchmark chỉ có ý nghĩa khi biết dùng file nào, model nào và cấu hình nào. Manifest lưu run fingerprint, model revision, chunk policy, metadata fingerprint và số lượng. File thay đổi không được lặng lẽ dùng index cũ.

Build mới ghi vào staging, hoàn tất mới đổi thành snapshot ready. Update metadata tạo snapshot khác; vector giữ nguyên vì nội dung embedding chưa đổi. Alias cùng ảnh có thể tái dùng vector nhưng vẫn giữ nguồn riêng để citation đúng.

Một model download xong không đồng nghĩa query nhanh. Có thời gian đọc weights, cấp phát GPU, encode, rerank, sinh token, đọc ảnh. Demo reload model theo request để dễ quản lý VRAM, nên latency gồm model load. Muốn service nhanh hơn cần thiết kế worker giữ model/cache, đo lại RAM/VRAM và concurrency.

## 12. Đánh giá đúng và giới hạn kết luận

50 QA hiện tại là development set tiếng Anh, không phải QA DENSO chính thức. Evidence draft dùng đáp án tìm substring trong OCR/chunk; trường hợp không tìm thấy giữ `not_located`, không bịa nhãn. Câu dịch Việt tự động là proxy chưa kiểm duyệt.

Hit@5: trong năm evidence đầu có ít nhất một vị trí gắn nhãn. MRR: nghịch đảo thứ hạng evidence đúng đầu tiên. Các số này đo tìm kiếm, không đo câu trả lời. Substring đáp án là proxy dễ tính, có thể chấm đúng cả câu chứa đáp án sai ngữ cảnh; không thay thế semantic correctness. P50/P95 là trung vị và mức bao phủ 95% latency; phải nêu số mẫu, cold/warm và bước đo.

So A/B/C, bật/tắt rerank, flat/table-aware, metadata boost, vision trên cùng bộ câu hỏi và phạm vi. Không tắt source filter để “tăng accuracy”. Bài test không dùng model kiểm tra cơ chế; benchmark model thật đo chất lượng/tốc độ. Cả hai cần thiết và phải báo riêng.

Chưa có tài liệu Nhật, chưa thể kết luận Nhật/Anh. Chưa có API/budget, chưa thể kết luận cloud rẻ/nhanh hơn. Chưa có metadata version thật, conflict chỉ có bằng chứng từ fixture. Các ngưỡng 20 giây text / 45 giây visual trong kế hoạch là mục tiêu khám phá, không phải cam kết đã đạt.

## 13. Cách phát triển tiếp từ prototype

Ưu tiên dựa trên lỗi quan sát: sửa OCR Việt khi dấu làm mất retrieval; crop ảnh độ phân giải cao khi bảng nhỏ; bổ sung metadata thật trước khi tăng hard filter; soát evidence labels trước khi dùng metric làm quyết định; mở rộng conflict extractor nếu có revision thật. Khi API được chọn, xác minh giao thức, chính sách dữ liệu, bảng giá và giới hạn chi trước khi benchmark trả phí. Khi quy mô tăng, thay local store bằng dịch vụ có payload index, hàng đợi và worker giữ model; vẫn giữ contract và provenance hiện có.
