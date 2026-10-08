import json
from pathlib import Path
import shutil

from ..io import write_text
from ..bt3.tables import table_html


def create_review(run_dir: Path, pages: list[dict], texts: list[dict], layouts: list[dict], metrics: dict):
    text_by_id = {x["page_id"]: x for x in texts}
    layout_by_id = {x["page_id"]: x for x in layouts}
    assessed = {x["page_id"]: x for x in metrics["pages"]}
    items = []
    for page in pages:
        image = None
        if page.get("image_sha256"):
            target = run_dir / "assets" / (page["image_sha256"] + ".png")
            if target.is_file():
                image = target.relative_to(run_dir).as_posix()
            elif page.get("image_path") and Path(page["image_path"]).is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(page["image_path"], target)
                image = target.relative_to(run_dir).as_posix()
        layout = layout_by_id.get(page["page_id"], {})
        items.append({"page": page, "image": image, "bt2": text_by_id.get(page["page_id"], {}),
                      "bt3": layout, "evaluation": assessed.get(page["page_id"], {}),
                      "table_html": [table_html(t) for t in layout.get("tables", [])]})
    data = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (Path(__file__).with_name("template.html")).read_text(encoding="utf-8")
    write_text(run_dir / "review.html", template.replace("__DATA__", data))
