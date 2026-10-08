from pathlib import Path
from dataclasses import replace
import json
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import streamlit as st
from denso_a3.backends.artifacts import read_json
from denso_a3.bt4.metadata import FIELDS
from denso_a3.bt4.metadata_enrichment import confirm_suggestions
from denso_a3.bt7.memory import ConversationStore
from denso_a3.io import write_json, fingerprint, sha256_file
from denso_a3.pipeline.index_builder import update_metadata
from denso_a3.pipeline.rag_config import load_config
from denso_a3.pipeline.rag_runner import ask

LOCAL = ROOT / ".local"
STATE = LOCAL / "workspace.json"
MEMORY = LOCAL / "conversations.sqlite"


def activate(path):
    write_json(STATE, {"index": str(Path(path).resolve())})


def run_command(args):
    process = subprocess.run([sys.executable, "-m", "denso_a3", *args], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout)[-2500:])


def scope_filters(raw_filters, sources):
    """Combine the selected documents with explicit filters without widening either."""
    filters = json.loads(raw_filters)
    if not isinstance(filters, dict):
        raise ValueError("Hard filters JSON phải là object")
    if sources is not None:
        if not sources:
            raise ValueError("Hãy chọn ít nhất một tài liệu")
        explicit = filters.get("source_relative")
        if explicit is not None:
            explicit = explicit if isinstance(explicit, list) else [explicit]
            sources = [source for source in sources if source in explicit]
            if not sources:
                raise ValueError("Phạm vi tài liệu và hard filters không có phần giao")
        filters["source_relative"] = sources
    return filters


def request_context_fingerprint(index, filters, config, memory_mode, conversation_id):
    """Invalidate UI results and instant pending when corpus, scope or config changes."""
    content = {name: sha256_file(Path(index) / name) for name in
        ("manifest.json", "chunks.json", "registry.json", "documents.json")
        if (Path(index) / name).is_file()}
    return fingerprint({"index": str(Path(index).resolve()), "content": content,
        "filters": filters, "config": config.to_dict(), "memory": memory_mode,
        "conversation_id": conversation_id})


def show_answer(answer, index):
    if answer["status"] in {"conflict", "abstain", "clarification"}:
        st.warning(answer["answer"])
    else:
        st.markdown(answer["answer"])
    st.caption(f"Trạng thái: {answer['status']} · {answer.get('timings_ms', {}).get('total', 0)/1000:.1f} giây")
    trace = answer.get("trace", {})
    verification = answer.get("verification", {})
    if verification:
        labels = {"verified": "Đã đối chiếu với nguồn", "rejected": "Chưa được nguồn chứng minh",
                  "error": "Chưa hoàn tất kiểm chứng", "disabled": "Kiểm chứng đang tắt",
                  "not_applicable": "Không có đáp án thành công cần kiểm chứng"}
        st.caption("Kiểm chứng: " + labels.get(verification.get("state"), verification.get("state", "")))
        if verification.get("claims") or verification.get("errors"):
            with st.expander("Kết quả kiểm chứng từng ý"):
                for claim in verification.get("claims", []):
                    st.write(claim.get("text", ""))
                    verdict_labels = {"supported": "Được nguồn chứng minh", "contradicted": "Nguồn bác bỏ",
                                      "insufficient": "Chưa đủ bằng chứng"}
                    st.caption(verdict_labels.get(claim.get("verdict"), claim.get("verdict", "")) + " · " + claim.get("reason", ""))
                for error in verification.get("errors", []):
                    st.caption(error)
    routing = trace.get("routing", trace.get("route_decision", {}))
    if routing:
        route = routing.get("final_route", routing.get("route", trace.get("selected_route", "")))
        reason = routing.get("reason") or ", ".join(routing.get("reason_codes", []))
        st.caption(f"Định tuyến: {route} · {reason}")
    if trace.get("rounds"):
        coverage = answer["trace"].get("coverage",{})
        st.caption(f"Tìm {len(answer['trace'].get('rounds',[]))} vòng · {answer['trace'].get('retrieval_calls',0)} truy vấn · "
            f"Điều kiện dừng: {answer['trace'].get('stop_reason','')}")
        if coverage.get("tasks"):
            st.dataframe(coverage["tasks"],hide_index=True)
    for i, citation in enumerate(answer.get("citations", [])):
        label = citation["source_relative"] + (f" — trang {citation['page_number']}" if citation["page_number"] else " — ảnh")
        with st.expander(f"[{citation['evidence_id']}] {label}"):
            image = Path(index) / citation["image_asset"]
            if image.is_file():
                st.image(str(image))
            st.caption("Bằng chứng OCR/ảnh chưa được người kiểm duyệt; kiểm tra số và đơn vị trên ảnh.")
    with st.expander("Chi tiết truy xuất / benchmark"):
        st.json(answer)


def main():
    st.set_page_config(page_title="DENSO · Document RAG", layout="wide")
    st.title("DENSO · Hỏi đáp tài liệu")
    st.caption("Prototype local · Anh / Việt · Dẫn nguồn tới file và trang/ảnh")
    default = read_json(STATE).get("index", "results/rag_main") if STATE.is_file() else "results/rag_main"
    with st.sidebar:
        index_text = st.text_input("Index hiện tại", value=default)
        config_text = st.text_input("File cấu hình", value="configs/rag.local.json")
        if st.button("Mở index"):
            path = Path(index_text).resolve()
            if (path / "manifest.json").is_file() and read_json(path / "manifest.json").get("state") == "ready":
                activate(path)
                st.session_state.pop("last_answer", None)
                st.success("Đã chọn index")
            else:
                st.error("Index chưa sẵn sàng")
        if st.button("Tạo snapshot phân tầng từ index này"):
            try:
                target = LOCAL / "indexes" / ("hierarchical_"+uuid.uuid4().hex[:12])
                with st.spinner("Đang tạo chỉ mục tài liệu và trang…"):
                    run_command(["rag-upgrade","--index",index_text,"--output",str(target),"--config",config_text])
                activate(target)
                st.success("Snapshot đã tạo. Nhập đường dẫn dưới đây vào Index hiện tại rồi bấm Mở index.")
                st.code(str(target))
            except Exception as exc:
                st.error(str(exc))
    index = Path(index_text).resolve()
    ready = (index / "manifest.json").is_file() and read_json(index / "manifest.json").get("state") == "ready"
    tabs = st.tabs(["Hỏi đáp", "Tài liệu & upload", "Metadata", "Cấu hình"])
    with tabs[3]:
        st.write("Hỏi đáp tự định tuyến và chỉ dùng model local. Keyword + semantic chạy nội bộ; ảnh được đọc khi cần bằng chứng.")
        st.code(Path(config_text).read_text(encoding="utf-8") if Path(config_text).is_file() else "Chưa có file cấu hình", language="json")
        st.caption("UI dùng auto, hybrid và visual auto. Cấu hình/API không tương thích sẽ báo lỗi; không chuyển sang cloud.")
    with tabs[1]:
        if ready:
            documents = read_json(index / "documents.json")
            st.dataframe([{"file": d["source_relative"], "id": d["document_id"]} for d in documents], hide_index=True)
            selected = st.selectbox("Tải lại tài liệu gốc", documents, format_func=lambda d: d["source_relative"])
            if selected.get("source_asset"):
                st.download_button("Tải file nguồn", (index / selected["source_asset"]).read_bytes(), file_name=Path(selected["source_relative"]).name)
        uploaded = st.file_uploader("Thêm PDF hoặc ảnh", type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"], accept_multiple_files=True, max_upload_size=50)
        ocr_language = st.selectbox("Ngôn ngữ OCR", ["en", "vi"], help="Chọn vi cho tài liệu tiếng Việt hoặc Việt/Anh. Chất lượng phải kiểm tra trên ảnh nguồn.")
        if st.button("Xử lý upload và tạo index mới", disabled=not uploaded):
            try:
                token = uuid.uuid4().hex[:12]
                batch = LOCAL / "uploads" / token
                batch.mkdir(parents=True)
                for i, item in enumerate(uploaded):
                    if item.size > 50*1024*1024:
                        raise ValueError("Mỗi file tối đa 50 MB")
                    name = Path(item.name).name
                    (batch / f"{token}_{i:03d}_{name}").write_bytes(item.getvalue())
                run = LOCAL / "runs" / token
                target = LOCAL / "indexes" / token
                with st.spinner("OCR, phân tích bảng, tạo mô tả ảnh và index…"):
                    # Unique filename prefix prevents IDs colliding across batches.
                    run_command(["run", "--input", str(batch), "--language", ocr_language, "--output", str(run)])
                    runs = [str(run)]
                    if ready:
                        runs = read_json(index / "manifest.json")["run_paths"] + runs
                    args = ["rag-build", "--run", *list(dict.fromkeys(runs)), "--output", str(target), "--config", config_text]
                    if ready:
                        args += ["--registry", str(index / "registry.json")]
                    run_command(args)
                activate(target)
                st.success("Index mới đã lưu. Tải lại trang để mở index mới.")
            except Exception as exc:
                st.error(str(exc))
        if not ready:
            base = st.text_input("Run BT1–BT3 đã hoàn tất", "results/full_200")
            if st.button("Tạo index đầu tiên"):
                try:
                    with st.spinner("Đang tạo index…"):
                        run_command(["rag-build", "--run", base, "--output", str(index), "--config", config_text])
                    activate(index)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
    with tabs[2]:
        if ready:
            registry = read_json(index / "registry.json")
            st.download_button("Xuất metadata JSON", json.dumps(registry, ensure_ascii=False, indent=2), "metadata.json")
            imported = st.file_uploader("Nhập metadata JSON", type="json")
            docid = st.selectbox("Tài liệu cần chỉnh", list(registry["documents"]), format_func=lambda k: registry["documents"][k]["source_relative"])
            st.caption("Gợi ý tự động chưa được áp dụng. Chỉ các trường bạn chọn và xác nhận mới trở thành metadata đã kiểm duyệt.")
            if st.button("Tạo lại gợi ý metadata"):
                try:
                    target = LOCAL / "indexes" / ("metadata_" + uuid.uuid4().hex[:12])
                    with st.spinner("Đang trích gợi ý từ nội dung tài liệu…"):
                        run_command(["rag-suggest-metadata", "--index", str(index), "--output", str(target), "--config", config_text])
                    activate(target)
                    st.success("Gợi ý đã lưu trong snapshot mới. Tải lại trang để xem.")
                except Exception as exc:
                    st.error(str(exc))
            suggestions_path = index / "metadata_suggestions.json"
            if suggestions_path.is_file():
                suggestions = read_json(suggestions_path)
                if suggestions.get("registry_fingerprint") != fingerprint(registry):
                    st.info("Gợi ý cũ không còn khớp metadata hiện tại. Bấm Tạo lại gợi ý metadata trước khi xác nhận tiếp.")
                else:
                    draft = suggestions.get("documents", {}).get(docid, {})
                    with st.form("metadata_suggestion_confirmation"):
                        selections = {}
                        for field, items in draft.get("fields", {}).items():
                            index_choice = st.selectbox("Gợi ý " + field, list(range(len(items))),
                                format_func=lambda i, items=items: items[i]["value"], key=f"suggestion:{index}:{docid}:{field}")
                            item = items[index_choice]
                            st.caption("Trang " + str(item["evidence"].get("page_number") or "ảnh") + " · " + item["evidence"]["quote"])
                            st.caption("; ".join(item.get("warnings", [])))
                            if st.checkbox("Xác nhận " + field, value=False, key=f"confirm:{index}:{docid}:{field}"):
                                selections[field] = index_choice
                        confirmed = st.form_submit_button("Xác nhận các gợi ý đã chọn")
                    for note in draft.get("notes", []):
                        st.caption(note)
                    if confirmed:
                        try:
                            updated = confirm_suggestions(registry, suggestions, read_json(index / "documents.json"),
                                read_json(index / "chunks.json"), docid, selections)
                            target = LOCAL / "indexes" / uuid.uuid4().hex[:12]
                            update_metadata(index, updated, target)
                            activate(target)
                            st.success("Chỉ các gợi ý đã chọn được xác nhận và lưu. Tải lại trang để mở snapshot mới.")
                        except Exception as exc:
                            st.error(str(exc))
            with st.form("metadata_form"):
                fields = registry["documents"][docid]["fields"]
                values = {}
                for field in FIELDS:
                    col1, col2 = st.columns([2, 1])
                    cell = fields.get(field, {})
                    value = col1.text_input(field, value=cell.get("value") or "")
                    statuses = ["unreviewed", "user_verified", "source_verified"]
                    status = col2.selectbox("Kiểm duyệt " + field, statuses, index=statuses.index(cell.get("review_status", "unreviewed")))
                    values[field] = {"value": value.strip() or None, "origin": "user_form", "review_status": status, "evidence": cell.get("evidence")}
                submitted = st.form_submit_button("Lưu form thành snapshot mới")
            import_clicked = st.button("Áp dụng JSON đã nhập", disabled=imported is None)
            if submitted or import_clicked:
                try:
                    if import_clicked:
                        registry = json.loads(imported.getvalue().decode("utf-8-sig"))
                    else:
                        registry["documents"][docid]["fields"] = values
                    target = LOCAL / "indexes" / uuid.uuid4().hex[:12]
                    update_metadata(index, registry, target)
                    activate(target)
                    st.success("Đã lưu metadata/index mới; tải lại trang để mở.")
                except Exception as exc:
                    st.error(str(exc))
    with tabs[0]:
        if not ready:
            st.info("Chưa có index. Mở tab Tài liệu & upload để tạo index.")
            return
        documents = read_json(index / "documents.json")
        scope_mode = st.radio("Phạm vi", ["Toàn bộ kho", "Chọn tài liệu"], horizontal=True)
        sources = st.multiselect("Tài liệu", [d["source_relative"] for d in documents]) if scope_mode == "Chọn tài liệu" else None
        memory_mode = st.radio("Hội thoại", ["Tức thì — mỗi câu độc lập", "Dài hạn — lưu và mở lại"], horizontal=True)
        cid = None
        if memory_mode.startswith("Dài hạn"):
            memory = ConversationStore(MEMORY)
            try:
                title = st.text_input("Tên hội thoại mới", "Cuộc trò chuyện mới")
                if st.button("Tạo hội thoại"):
                    st.session_state["conversation_id"] = memory.create(title)
                conversations = memory.list()
                if conversations:
                    ids = [c["id"] for c in conversations]
                    labels = {c["id"]: c["title"] + " · " + c["created"][:16] for c in conversations}
                    cid = st.selectbox("Mở hội thoại", ids, index=ids.index(st.session_state.get("conversation_id")) if st.session_state.get("conversation_id") in ids else 0, format_func=labels.get)
                    for turn in memory.history(cid, limit=10000):
                        st.chat_message("user").write(turn["question"])
                        with st.chat_message("assistant"):
                            show_answer(turn["answer"], turn["answer"].get("trace", {}).get("index", index))
            finally:
                memory.close()
        with st.expander("Bộ lọc nâng cao"):
            raw_filters = st.text_area("Hard filters JSON (chỉ metadata đã xác minh)", "{}")
        selected_config, context_key = None, None
        try:
            selected_config = replace(load_config(config_text), orchestration_mode="auto",
                local_only=True, retrieval_strategy="hybrid", visual_policy="auto")
            selected_config.validate()
            filters = scope_filters(raw_filters, sources)
            context_key = request_context_fingerprint(index, filters, selected_config, memory_mode, cid)
        except Exception as exc:
            st.error(str(exc))
        if st.session_state.get("pending_context") != context_key:
            st.session_state.pop("pending_research", None)
            st.session_state.pop("pending_context", None)
        question = st.text_area("Câu hỏi (English / Tiếng Việt)")
        if st.button("Trả lời", type="primary", disabled=not question.strip() or selected_config is None or context_key is None):
            try:
                if memory_mode.startswith("Dài hạn") and not cid:
                    raise ValueError("Hãy tạo hội thoại trước")
                with st.spinner("Đang tìm bằng chứng và trả lời…"):
                    stage_status = st.empty()
                    scope_pending = st.session_state.get("pending_research")
                    answer = ask(index=index, question=question, config=selected_config,
                        filters=filters, conversation_id=cid, memory_path=MEMORY if cid else None,
                        pending=scope_pending if not cid else None, progress=stage_status.info)
                    stage_status.empty()
                st.session_state["pending_research"] = answer.get("trace",{}).get("pending")
                st.session_state["pending_context"] = context_key
                st.session_state["last_answer"] = {"answer": answer, "context": context_key}
            except Exception as exc:
                st.error(str(exc))
        result = st.session_state.get("last_answer")
        if result and context_key is not None and result.get("context") == context_key:
            show_answer(result["answer"], index)


if __name__ == "__main__":
    main()
