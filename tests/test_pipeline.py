import json
import multiprocessing as mp
from pathlib import Path
import time

import pytest
from PIL import Image
import pymupdf

from denso_a3.contracts import PipelineConfig, PageInput, checked_box
from denso_a3.bt1.ingestion import ingest, inspect_pdf_page, exif_affine
from denso_a3.backends.normalization import bbox, normalize_payload
from denso_a3.bt3.tables import table_html, continuation_candidates
from denso_a3.evaluation.text import compare, aggregate
from denso_a3.evaluation.evaluator import split_reference, evaluate
from denso_a3.evaluation.structural import match_boxes, evaluate_tables, evaluate_layout
from denso_a3.pipeline.runner import bind_payload, cache_key, failure_payload
from denso_a3.pipeline.worker import InferenceWorker
from denso_a3.io import write_json
from denso_a3.review.html import create_review

PROJECT = Path(__file__).resolve().parents[1]


def config(root, **kwargs):
    return PipelineConfig(str(root), str(root/"cache"), str(root/"models"), **kwargs)


def test_full_dataset_ingestion_and_hidden_layer(tmp_path):
    cfg = PipelineConfig(str(PROJECT/"evaluation/data"), str(tmp_path/"cache"), str(tmp_path/"models"))
    docs, pages, summary = ingest(cfg)
    assert (len(docs),len(pages),summary["unique_canonical_images"]) == (51,67,47)
    assert all(p.status == "ok" for p in pages)
    pdf = [p for p in pages if p.source_relative == "nhwc0428.pdf"]
    assert len(pdf) == 17
    assert all(p.metadata["content_type"] == "scan_with_ocr" and p.metadata["hidden_text_chars"] > 0 for p in pdf)
    assert all(p.metadata["route_policy"] == "force_fresh_ocr" for p in pdf)
    assert all("answers" not in p.metadata and "question" not in p.metadata for p in pages)
    assert len({p.page_id for p in pages}) == 67


def test_ingestion_aliases_sidecars_corrupt_and_unsupported(tmp_path):
    Image.new("RGB",(20,30),"white").save(tmp_path/"a.png")
    (tmp_path/"b.png").write_bytes((tmp_path/"a.png").read_bytes())
    (tmp_path/"broken.pdf").write_bytes(b"%PDF- broken")
    (tmp_path/"broken.png").write_bytes(b"broken")
    (tmp_path/"ignored.ocr").write_text("REFERENCE MUST NOT BE INGESTED")
    (tmp_path/"unsupported.docx").write_bytes(b"no")
    docs,pages,summary=ingest(config(tmp_path))
    assert len(docs)==5 and len(pages)==3
    assert summary["unique_canonical_images"]==1
    assert summary["duplicate_page_records"]==1
    assert {x["source_relative"] for x in docs if x["status"]=="failed"}=={"broken.pdf","unsupported.docx"}
    assert next(p for p in pages if p.source_relative=="broken.png").status=="failed"
    assert len(next(p for p in pages if p.source_relative=="a.png").metadata["alias_page_ids"])==2


def test_exif_rotation_dimensions(tmp_path):
    image=Image.new("RGB",(20,30),"white")
    exif=Image.Exif();exif[274]=6
    image.save(tmp_path/"rotated.jpg",exif=exif)
    _,pages,_=ingest(config(tmp_path))
    assert (pages[0].width,pages[0].height)==(30,20)
    assert pages[0].metadata["source_to_canonical"]==[0,1,-1,0,30,0]


@pytest.mark.parametrize("rotation", [0,90,180,270])
def test_pdf_rotation_crop_geometry(rotation):
    # Changes are in memory only; source PDF is never saved/rewritten.
    with pymupdf.open(PROJECT/"evaluation/data/nhwc0428.pdf") as doc:
        page=doc[0]
        page.set_cropbox(pymupdf.Rect(20,30,550,800))
        page.set_rotation(rotation)
        scale=pymupdf.Matrix(200/72,200/72)
        matrix=page.rotation_matrix*scale
        pix=page.get_pixmap(dpi=200,alpha=False)
        matrix.e-=pix.x;matrix.f-=pix.y
        rect=pymupdf.Rect(0,0,530,770)*matrix
        assert abs(rect.width-pix.width)<=1 and abs(rect.height-pix.height)<=1
        point=pymupdf.Point(100,150)
        restored=point*matrix*(~matrix)
        assert abs(restored.x-point.x)<.001 and abs(restored.y-point.y)<.001


def test_bottom_left_normalization_and_invalid_box():
    assert bbox({"l":10,"t":90,"r":30,"b":50,"coord_origin":"BOTTOMLEFT"},100,100)==[.1,.1,.3,.5]
    with pytest.raises(ValueError):checked_box([0,0,120,20],100,100)
    with pytest.raises(ValueError):checked_box([0,0,0,20],100,100)


def test_geometry_error_preserves_text_without_fabricating_box():
    cells=[{"text":"VISIBLE","bbox":{"l":0,"t":0,"r":200,"b":20},"confidence":.9}]
    text,layout=normalize_payload({},cells,(100,100),[],"success",[])
    assert text["text"]=="VISIBLE" and text["spans"]==[]
    assert text["status"]=="partial" and layout["status"]=="partial"


def test_sparse_table_grid_stays_unknown_and_partial():
    raw={"tables":[{"self_ref":"#/tables/0","label":"table","prov":[{"page_no":1,"bbox":{"l":0,"t":0,"r":100,"b":100}}],
                    "data":{"num_rows":1,"num_cols":2,"table_cells":[{"start_row_offset_idx":0,"start_col_offset_idx":0,"text":"known"}]}}]}
    _,layout=normalize_payload(raw,[],(100,100),["#/tables/0"],"success",[])
    table=layout["tables"][0]
    assert layout["status"]==table["status"]=="partial"
    assert table["unknown_grid_positions"]==[{"row_index":0,"column_index":1,"content_status":"unknown"}]
    assert len(table["cells"])==1 and table["grid_coverage"]==.5


def test_form_feed_alignment_preserves_blank():
    assert split_reference("a\f\fb\f",3)==["a","","b"]
    with pytest.raises(ValueError):split_reference("a\fb",3)


def test_cer_wer_deletions_insertions_and_empty():
    value=compare("abc def","abc xyz ghi","whitespace")
    assert value["wer"]["rate"]==1 and value["wer"]["edits"]==2
    assert compare("","abc","raw")["cer"]["rate"] is None
    assert compare("a","aaaa","raw")["cer"]["rate"]==3
    assert aggregate([compare("abc","abc","raw"),compare("x","","raw")])["cer"]["micro"]==.25


def test_reference_mismatch_is_not_scored(tmp_path):
    source=tmp_path/"input";source.mkdir();(source/"a.ocr").write_text("one\ftwo")
    run=tmp_path/"run"
    write_json(run/"documents.json",[{"document_id":"d","format":"pdf","source_relative":"a.pdf","page_count":3}])
    write_json(run/"pages.json",[{"document_id":"d","page_id":"d:1","page_number":1}])
    write_json(run/"bt2.json",[{"page_id":"d:1","text":"one","status":"ok"}]);write_json(run/"bt3.json",[])
    result=evaluate(run,source)
    assert result["evaluated_pages"]==0
    assert result["reference_registry"][0]["status"]=="unaligned"


def test_review_reuses_copied_image_after_run_relocation(tmp_path):
    asset = tmp_path/"assets"/"sha.png"
    asset.parent.mkdir()
    asset.write_bytes(b"existing review image")
    page = {"page_id":"p", "image_sha256":"sha", "image_path":str(tmp_path/"old_cache"/"sha.png")}
    create_review(tmp_path, [page], [], [], {"pages":[]})
    html = (tmp_path/"review.html").read_text(encoding="utf-8")
    assert 'assets/sha.png' in html
    assert asset.read_bytes() == b"existing review image"


def test_alias_rebinding_does_not_mutate_cached_payload():
    payload={"bt2":{"spans":[{"span_id":"s0"}]},"bt3":{"blocks":[{"block_id":"b0","span_ids":["s0"]}],
              "tables":[{"table_id":"t0","block_id":"b0","cells":[{"span_ids":["s0"]}]}]}}
    page=PageInput("alias:p1","alias","source","source","sha",1,"pdf_scan")
    bound=bind_payload(payload,page)
    assert payload["bt2"]["spans"][0]["span_id"]=="s0"
    assert bound["bt3"]["tables"][0]["cells"][0]["span_ids"]==["alias:p1:s0"]


def test_cache_changes_for_image_model_configuration(tmp_path):
    page=PageInput("a","d","s","s","sha",1,"image",image_sha256="img")
    runtime={"versions":{"torch":"test"},"model_fingerprint":"m1"}
    a=cache_key(page,config(tmp_path),runtime,"code1")
    assert a!=cache_key(page,config(tmp_path,dpi=300),runtime,"code1")
    assert a!=cache_key(page,config(tmp_path),{**runtime,"model_fingerprint":"m2"},"code1")
    page.page_id="different_alias"
    assert a==cache_key(page,config(tmp_path),runtime,"code1")


def test_merged_cell_html_and_escaping():
    t={"num_rows":2,"num_columns":2,"cells":[{"row_index":0,"column_index":0,"rowspan":2,"colspan":1,"text_raw":"<script>","column_header":True},
       {"row_index":0,"column_index":1,"rowspan":1,"colspan":1,"text_raw":"value"}]}
    html=table_html(t)
    assert 'rowspan="2"' in html and '&lt;script&gt;' in html and '<script>' not in html
    assert html.count('class="unknown"')==1


def test_missing_table_penalizes_cells_and_partial_labels_hide_precision():
    p={"page_id":"p","source_relative":"a.pdf","page_number":1,"source_sha256":"sha"}
    annotation={"source_relative":"a.pdf","page_number":1,"source_sha256":"sha","table_detection_exhaustive":False,
        "tables":[{"annotation_id":"t","bbox":[0,0,1,1],"num_rows":1,"num_columns":1,"cell_annotation_exhaustive":True,
        "cells":[{"row_index":0,"column_index":0,"text_raw":"","content_status":"verified_empty"}]}]}
    result=evaluate_tables({"pages":[annotation]},[p],[])
    assert result["recall"]==0 and result["precision"] is None
    assert result["visible_cell_exact_match"]==0 and result["complete_grid_topology_exact"]==0


def test_matching_is_one_to_one_and_layout_class_aware():
    t=[{"bbox":[0,0,1,1]}]*2
    assert len(match_boxes(t,[{"bbox":[0,0,1,1]}]))==1
    page={"page_id":"p","source_relative":"a","page_number":1,"source_sha256":"s"}
    ann={"source_relative":"a","page_number":1,"source_sha256":"s","layout":[{"annotation_id":"a","type":"figure","bbox":[0,0,1,1]}]}
    result=evaluate_layout({"pages":[ann]},[page],[{"page_id":"p","blocks":[{"type":"table","bbox":[0,0,1,1]}]}])
    assert result["matched_blocks"]==0 and result["map"] is None


def test_timeout_terminates_worker(tmp_path):
    worker=InferenceWorker(config(tmp_path))
    context=mp.get_context("spawn")
    worker.connection,child=context.Pipe()
    worker.process=context.Process(target=time.sleep,args=(30,))
    worker.process.start()
    process=worker.process
    try:
        with pytest.raises(TimeoutError):worker._receive(.02,"test")
        assert worker.process is None
    finally:
        child.close()
        worker.close()
