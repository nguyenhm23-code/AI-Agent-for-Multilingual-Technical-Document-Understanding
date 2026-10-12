"""One portable HTML artifact for a small, sequential, local RAG benchmark.

Expected answers are review material, never inference input or automatic accuracy.
The embedded JSON is the complete record; HTML is a readable view of that record.
"""
from collections import Counter
from datetime import datetime, timezone, timedelta
from html import escape
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
import uuid

from ..backends.artifacts import read_json
from ..io import fingerprint, sha256_file, write_text
from ..pipeline.rag_runner import ask

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"answered", "abstain", "clarification", "conflict"}
PAYLOAD_START = '<script type="application/json" id="evaluation-data">'
LIMITATIONS = [
    "Tỷ lệ trả lời, có trích nguồn và vượt kiểm chứng không phải độ chính xác nội dung.",
    "Đáp án tham khảo chỉ để người review đối chiếu; không được gửi vào model và chưa được chấm tự động.",
    "Đo tuần tự, mỗi câu độc lập, không dùng lịch sử hội thoại hoặc tiếp tục câu làm rõ.",
    "Độ trễ là thời gian gọi ask, gồm tải model; không gồm ghi báo cáo, ingestion hoặc build index. Cache hệ điều hành có thể đã ấm.",
    "P95 nội suy tuyến tính trên mẫu nhỏ; không phải SLA. Báo riêng độ trễ câu đã trả lời và các lượt lỗi.",
    "Báo cáo giữ kết quả, nguồn trích và fingerprint; không đóng gói ảnh gốc, toàn bộ index hay weights. Giữ snapshot để đối chiếu sau này.",
    "Đây là lượt thử do người dùng chọn câu hỏi, chưa mặc nhiên là tập hold-out đại diện cho DENSO.",
]


def now():
    return datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds")


def load_questions(path, limit=None):
    data = read_json(Path(path))
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError("File câu hỏi cần object có danh sách questions")
    if limit is not None and limit < 1:
        raise ValueError("limit phải >= 1")
    rows, ids, skipped = [], set(), 0
    for row in data["questions"]:
        if not isinstance(row, dict) or not isinstance(row.get("question", ""), str):
            raise ValueError("Mỗi câu phải là object, question phải là chuỗi")
        if not row.get("question", "").strip():
            skipped += 1
            continue
        if "assumptions" in row:
            raise ValueError("Ghi điều kiện trực tiếp trong question; không dùng assumptions")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            raise ValueError("Mỗi câu đã điền cần id chuỗi không rỗng, không trùng")
        ids.add(identifier)
        if not isinstance(row.get("filters", {}), dict):
            raise ValueError(f"{identifier}: filters phải là object")
        if row.get("expected_status") not in (None, "", *STATUSES):
            raise ValueError(f"{identifier}: expected_status không hợp lệ")
        for key in ("category", "expected_answer", "reference"):
            if key in row and not isinstance(row[key], str):
                raise ValueError(f"{identifier}: {key} phải là chuỗi")
        rows.append(dict(row))
    if not rows:
        raise ValueError("Chưa có câu hỏi. Điền question trong file trước khi chạy; dòng rỗng được bỏ qua.")
    return data, rows[:limit] if limit else rows, skipped


def percentiles(values):
    values = sorted(values)
    def percentile(p):
        pos = (len(values) - 1) * p
        lo, hi = math.floor(pos), math.ceil(pos)
        return values[lo] + (values[hi] - values[lo]) * (pos - lo)
    return {"n": len(values), "mean": sum(values) / len(values),
            "p50": percentile(.5), "p95": percentile(.95), "max": values[-1]} if values else {
                "n": 0, "mean": None, "p50": None, "p95": None, "max": None}


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "percent": 100 * numerator / denominator if denominator else None}


def summarize(records, planned):
    counts = Counter(r["status"] for r in records)
    answered = [r for r in records if r["status"] == "answered"]
    finished = [r for r in records if r["status"] != "interrupted"]
    expected = [r for r in records if r["test"].get("expected_status")]
    token_values = [r["result"].get("usage", {}).get("total_tokens") for r in records if r.get("result")]
    token_values = [v for v in token_values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    latency = lambda rows: percentiles([r["wall_seconds"] for r in rows])
    return {
        "planned": planned, "attempted": len(records), "finished": len(finished),
        "not_attempted": planned - len(records), "status_counts": dict(counts),
        "answered_rate": ratio(len(answered), len(records)),
        "runtime_error_rate": ratio(counts["error"], len(records)),
        "answered_with_citations": ratio(sum(bool(r["result"].get("citations")) for r in answered), len(answered)),
        "answered_verified_by_model": ratio(sum(r["result"].get("verification", {}).get("state") == "verified" for r in answered), len(answered)),
        "expected_status_agreement": ratio(sum(r["status"] == r["test"]["expected_status"] for r in expected), len(expected)),
        "semantic_accuracy": None, "semantic_accuracy_state": "requires_human_review",
        "latency_seconds": {"finished_including_errors": latency(finished), "answered_only": latency(answered),
            "non_answered_without_errors": latency([r for r in finished if r["status"] in STATUSES - {"answered"}]),
            "runtime_errors": latency([r for r in finished if r["status"] == "error"])},
        "total_ask_seconds_including_interruptions": sum(r["wall_seconds"] for r in records),
        "total_tokens": {"value": sum(token_values) if token_values else None, "requests_measured": len(token_values)},
        "routes": dict(Counter(r["result"].get("trace", {}).get("routing", {}).get("final_route") or
            r["result"].get("trace", {}).get("routing", {}).get("route") or "unreported" for r in records if r.get("result"))),
    }


def environment_snapshot(index, config):
    packages = {}
    for name in ("torch", "docling-slim", "qdrant-client", "transformers", "streamlit"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    def command(args):
        try:
            result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    code_files = {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in sorted((ROOT / "denso_a3").rglob("*.py"))}
    hashes = {name: sha256_file(index / name) for name in
              ("manifest.json", "registry.json", "documents.json", "chunks.json", "hierarchy.json") if (index / name).is_file()}
    return {"python": platform.python_version(), "os": platform.platform(), "cpu": platform.processor(),
        "logical_cpus": os.cpu_count(), "packages": packages,
        "gpu_name_memory_mib_driver": command(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]),
        "git_commit": command(["git", "rev-parse", "HEAD"]),
        "code_sha256": fingerprint(code_files), "source_files_sha256": code_files,
        "index": str(index), "index_files_sha256": hashes,
        "index_manifest": read_json(index / "manifest.json"),
        "configured_model_manifest": read_json(Path(config.model_manifest)) if Path(config.model_manifest).is_file() else None,
        "config": config.to_dict()}


def render_report(report):
    e = lambda value: escape(str(value), quote=True)
    summary = report["summary"]
    def display(value):
        return "Chưa đo" if value is None else f"{value:.2f}"
    def rate(value):
        return "Chưa đo" if value["percent"] is None else f'{value["numerator"]}/{value["denominator"]} · {value["percent"]:.1f}%'
    cards = [
        ("Câu đã kết thúc / dự kiến", f'{summary["finished"]}/{summary["planned"]}'),
        ("Có trả lời / lượt đã thử", rate(summary["answered_rate"])),
        ("Lỗi thực thi / lượt đã thử", rate(summary["runtime_error_rate"])),
        ("Đáp án có citation / đáp án", rate(summary["answered_with_citations"])),
        ("Đáp án qua kiểm chứng model", rate(summary["answered_verified_by_model"])),
        ("Độ đúng nội dung", "Chưa chấm bởi người review"),
    ]
    cards_html = "".join(f'<div class="card"><span>{e(label)}</span><strong>{e(value)}</strong></div>' for label, value in cards)
    labels = {"finished_including_errors": "Mọi lượt đã kết thúc, gồm lỗi", "answered_only": "Chỉ lượt có đáp án",
              "non_answered_without_errors": "Từ chối / làm rõ / xung đột", "runtime_errors": "Lỗi thực thi"}
    latency_rows = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in
        (labels[key], data["n"], *(display(data[k]) for k in ("mean", "p50", "p95", "max")))) + "</tr>"
        for key, data in summary["latency_seconds"].items())
    details = []
    for record in report["records"]:
        test, result = record["test"], record.get("result") or {}
        citations = "\n".join(f'{c.get("source_relative", "?")} — trang {c.get("page_number") or "ảnh"} — {c.get("evidence_id", "")}' for c in result.get("citations", []))
        details.append(f'''<details><summary>{e(test['id'])} · {e(record['status'])} · {record['wall_seconds']:.2f} s — {e(test['question'])}</summary>
<p><b>Nhóm:</b> {e(test.get('category', ''))}</p>
<h3>Đáp án hệ thống</h3><pre>{e(result.get('answer', record.get('error', 'Lượt bị ngắt')))}</pre>
<h3>Đáp án tham khảo — chưa tự động chấm</h3><pre>{e(test.get('expected_answer') or 'Chưa cung cấp')}</pre>
<p><b>Trạng thái kỳ vọng:</b> {e(test.get('expected_status') or 'Chưa cung cấp')}</p>
<p><b>Nguồn tham khảo:</b> {e(test.get('reference') or 'Chưa cung cấp')}</p>
<h3>Nguồn hệ thống trích</h3><pre>{e(citations or 'Không có')}</pre>
<details><summary>Toàn bộ dữ liệu lượt này</summary><pre>{e(json.dumps(record, ensure_ascii=False, indent=2))}</pre></details></details>''')
    status_html = "".join(f"<li>{e(status)}: <b>{count}</b></li>" for status, count in summary["status_counts"].items())
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    limitations = "".join(f"<li>{e(item)}</li>" for item in report["limitations"])
    state = {"running": "ĐANG CHẠY — số liệu tạm thời", "complete": "ĐÃ CHẠY HẾT BỘ CÂU HỎI",
             "interrupted": "BỊ NGẮT — chưa hoàn tất bộ câu hỏi"}[report["state"]]
    return f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DENSO A3 · Báo cáo đánh giá</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6fa;color:#18243a;line-height:1.6}}main{{max-width:1120px;margin:auto;padding:32px 24px}}
h1{{font-size:30px;margin-bottom:8px}}h2{{margin-top:30px}}h3{{font-size:16px}}.meta{{color:#53627a}}.banner{{padding:14px 18px;border-left:5px solid #19716e;background:#e3f0f0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:24px 0}}.card,details{{background:white;border:1px solid #dbe2ec;border-radius:8px;padding:16px}}
.card span{{display:block;color:#53627a;font-size:14px}}.card strong{{display:block;font-size:23px;margin-top:6px}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:11px;text-align:left;border-bottom:1px solid #dbe2ec}}th{{background:#e7edf5}}
.table{{overflow-x:auto}}details{{margin:12px 0}}summary{{cursor:pointer;font-weight:600;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 Consolas,monospace}}li{{margin:6px 0}}
@media print{{body{{background:white}}main{{padding:0}}.card{{break-inside:avoid}}}}
</style></head><body><main><p class="meta">DENSO A3 · Lượt thử {e(report['run_id'])}</p>
<h1>{e(report['title'])}</h1><p class="meta">Bắt đầu: {e(report['started_at'])} · Cập nhật: {e(report['updated_at'])} (UTC+7)</p>
<p class="banner">{state}. Lượt đang xử lý: {e(report.get('in_progress') or 'không có')}. Lỗi thực thi vẫn được giữ trong báo cáo.</p>
{cards_html}<h2>Số liệu dùng để chuẩn bị slide</h2>
<p>Khớp trạng thái kỳ vọng: <b>{e(rate(summary['expected_status_agreement']))}</b> — chỉ so trạng thái, không chấm nội dung.</p>
<p>Tổng thời gian gọi hệ thống: <b>{summary['total_ask_seconds_including_interruptions']:.2f} giây</b>.
Token ghi nhận: <b>{e(summary['total_tokens']['value'] if summary['total_tokens']['value'] is not None else 'Chưa đo')}</b>
trên {summary['total_tokens']['requests_measured']} lượt có số đo. Không suy ra chi phí tiền từ token local.</p>
<ul>{status_html}</ul><h2>Độ trễ — giây</h2><div class="table"><table><thead><tr><th>Phạm vi</th><th>Số lượt</th><th>Trung bình</th><th>P50</th><th>P95</th><th>Lớn nhất</th></tr></thead><tbody>{latency_rows}</tbody></table></div>
<h2>Cách đọc kết quả</h2><ul>{limitations}</ul>
<h2>Chi tiết từng câu</h2>{''.join(details)}
<details><summary>Cấu hình và bằng chứng tái hiện</summary><pre>{e(json.dumps(report['provenance'], ensure_ascii=False, indent=2))}</pre></details>
<p class="meta">Một file HTML độc lập, không cần mạng. Dữ liệu JSON đầy đủ được nhúng trong evaluation-data để phân tích lại. Báo cáo chưa chứa kết quả chấm ngữ nghĩa của con người.</p>
{PAYLOAD_START}{payload}</script></main></body></html>'''


def read_report(path):
    """Recover the exact JSON from an exported report without a browser or models."""
    html = Path(path).read_text(encoding="utf-8")
    return json.loads(html.split(PAYLOAD_START, 1)[1].split("</script>", 1)[0])


def evaluate_for_slides(index, questions_path, output, config, limit=None):
    config.validate()
    if not config.local_only or config.generation_provider != "local":
        raise ValueError("Đánh giá slide yêu cầu local_only=true và generation_provider=local")
    source, questions, skipped = load_questions(questions_path, limit)
    root, target = Path(index).resolve(), Path(output).resolve()
    if target.suffix.lower() != ".html":
        raise ValueError("Output phải là file .html mới")
    if target.exists():
        raise ValueError("Báo cáo đã tồn tại; chọn file mới để giữ bằng chứng cũ")
    manifest = read_json(root / "manifest.json")
    if manifest.get("state") != "ready":
        raise ValueError("Index chưa ready. Hoàn tất build trước khi đánh giá")
    report = {"schema_version": "slide-evaluation-1.0", "run_id": uuid.uuid4().hex[:12],
        "title": str(source.get("title") or "Đánh giá prototype DENSO A3"), "state": "running",
        "started_at": now(), "updated_at": now(), "in_progress": None,
        "question_file": str(Path(questions_path).resolve()), "question_file_sha256": sha256_file(Path(questions_path)),
        "question_file_snapshot": source, "selected_questions": questions, "blank_rows_skipped": skipped,
        "provenance": environment_snapshot(root, config), "limitations": list(LIMITATIONS), "records": []}
    if not 20 <= len(questions) <= 30:
        report["limitations"].append(f"Lượt này có {len(questions)} câu, ngoài khoảng 20–30 câu dự kiến; không coi là benchmark đầy đủ.")
    def save():
        report["updated_at"] = now()
        report["summary"] = summarize(report["records"], len(questions))
        write_text(target, render_report(report))
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation also protects against two evaluators choosing the same path.
    with target.open("x", encoding="utf-8"):
        pass
    save()
    for number, question in enumerate(questions, 1):
        report["in_progress"] = question["id"]
        save()
        record = {"test": question, "started_at": now()}
        tick = time.perf_counter()
        try:
            answer = ask(index=root, question=question["question"], config=config, filters=question.get("filters", {}))
            if not isinstance(answer, dict) or answer.get("status") not in STATUSES:
                raise ValueError("Hệ thống trả kết quả sai schema/status")
            record.update(status=answer["status"], result=answer)
        except KeyboardInterrupt:
            record.update(status="interrupted", error="Người dùng ngắt lượt chạy; chưa có đáp án cuối")
            report["state"] = "interrupted"
        except Exception as exc:
            record.update(status="error", error=f"{type(exc).__name__}: {exc}")
        record["wall_seconds"] = time.perf_counter() - tick
        record["finished_at"] = now()
        report["records"].append(record)
        report["in_progress"] = None
        if number == len(questions) and report["state"] != "interrupted":
            report["state"] = "complete"
        save()
        print(f'{number}/{len(questions)} · {question["id"]} · {record["status"]} · {record["wall_seconds"]:.2f}s', flush=True)
        if report["state"] == "interrupted":
            break
    return {"report": str(target), "state": report["state"], **report["summary"]}
