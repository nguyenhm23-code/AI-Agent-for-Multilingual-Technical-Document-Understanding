"""Read immutable BT1–BT3 runs, including runs moved with the repository."""

import json
from pathlib import Path

from ..contracts import SCHEMA_VERSION
from ..io import fingerprint, sha256_file


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_run(run_dir: str | Path) -> dict:
    root = Path(run_dir).resolve()
    names = ("pages", "documents", "bt2", "bt3", "run_config", "runtime", "performance")
    data = {name: read_json(root / (name + ".json")) for name in names}
    if data["run_config"].get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported BT1–BT3 schema")
    if "total_wall_seconds" not in data["performance"] or data["performance"].get("startup_error"):
        raise ValueError("Extraction run is incomplete or model startup failed")
    pages = data["pages"]
    ids = [p["page_id"] for p in pages]
    if len(set(ids)) != len(ids) or len(ids) != data["run_config"]["selected_pages"]:
        raise ValueError("Invalid or incomplete page inventory")
    for stage in ("bt2", "bt3"):
        rows = data[stage]
        if len(rows) != len(ids) or {r["page_id"] for r in rows} != set(ids):
            raise ValueError(f"{stage}: page IDs do not match inventory")
        data[stage + "_by_page"] = {r["page_id"]: r for r in rows}
    data["root"] = root
    data["fingerprint"] = fingerprint({n: sha256_file(root / (n + ".json")) for n in names})
    return data


def page_image(run: dict, page: dict) -> Path:
    """Prefer copied run assets; verify bytes instead of trusting an old path."""
    expected = page.get("image_sha256")
    candidates = [run["root"] / "assets" / f"{expected}.png"]
    if page.get("image_path"):
        candidates.append(Path(page["image_path"]))
    for candidate in candidates:
        if candidate.is_file() and sha256_file(candidate) == expected:
            return candidate.resolve()
    raise FileNotFoundError(f"Canonical image missing/hash mismatch: {page['page_id']}")
