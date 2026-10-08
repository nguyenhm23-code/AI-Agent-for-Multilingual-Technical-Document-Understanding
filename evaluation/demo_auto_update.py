"""Reproducible real-local-model demo. Labels are agent-reviewed synthetic sources.

Run from project root: .venv/Scripts/python.exe evaluation/demo_auto_update.py
No expected labels are passed into ask(). Production snapshot is read-only.
"""
import argparse
import json
import sys
import time
import hashlib
import statistics
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from denso_a3.pipeline.rag_config import load_config
from denso_a3.pipeline.rag_runner import ask
from denso_a3.bt5.store import RetrievalStore
from denso_a3.bt5.hierarchy_store import HierarchyStore
from denso_a3.bt4.hierarchy import build_hierarchy
from denso_a3.backends.rag_models import TextEncoder
from denso_a3.backends.artifacts import read_json
from denso_a3.backends.generation import Generator
from denso_a3.pipeline import rag_runner


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")


def prepare(root, config):
    if (root/"manifest.json").is_file():
        return
    root.mkdir(parents=True,exist_ok=True)
    (root/"assets").mkdir(exist_ok=True)
    model_manifest=read_json(Path(config.model_manifest))
    font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",44)
    rows=[]
    sources=[("text_m1","M1 rated voltage: 24 V", "text"),
             ("alpha","Alpha Latest Estimate cost: GBP100", "text"),
             ("beta","Beta Latest Estimate cost: GBP200", "text"),
             ("visual_m1","M1 rated voltage: 24 V", "page_visual"),
             ("visual_m2","M2 rated voltage: 12 V", "page_visual")]
    for name,literal,kind in sources:
        im=Image.new("RGB",(1100,300),"white")
        ImageDraw.Draw(im).text((30,100),literal,font=font,fill="black")
        asset=root/(name+".png"); im.save(asset)
        sha=hashlib.sha256(asset.read_bytes()).hexdigest()
        asset.replace(root/"assets"/(sha+".png"))
        text=literal if kind=="text" else "UNVERIFIED VISUAL DESCRIPTION: "+name.replace("visual_","").upper()+" rated voltage diagram. Read original source image."
        rows.append({"chunk_id":name,"content_key":name,"document_id":name,"source_relative":name+".png",
                     "source_sha256":sha,"page_id":name+":p1","page_number":None,"image_sha256":sha,
                     "kind":kind,"text":text,"metadata":{},"bbox":[0,0,1,1],"parent_id":name+":p1","quality_flags":["synthetic_demo_source"]})
    docs=[{"document_id":r["document_id"],"source_relative":r["source_relative"],"source_sha256":r["source_sha256"]} for r in rows]
    nodes=build_hierarchy(rows,docs)
    spec=model_manifest["models"]["embedding"]
    encoder=TextEncoder(spec["path"],spec["repo"],config.query_device)
    try:
        vectors=encoder.encode([r["text"] for r in rows])
        node_vectors=encoder.encode([n["routing_text"] for n in nodes])
    finally:
        encoder.close()
    with RetrievalStore(root,create=True,dimension=vectors.shape[1]) as store:
        store.add(rows,vectors)
        hierarchy=HierarchyStore(store,create=True)
        try: hierarchy.add(nodes,node_vectors)
        finally: hierarchy.close()
    write_json(root/"chunks.json",rows)
    write_json(root/"documents.json",docs)
    write_json(root/"registry.json",{"documents":{d["document_id"]:{**d,"fields":{}} for d in docs}})
    write_json(root/"manifest.json",{"state":"ready","schema_version":"1.1","hierarchy_version":"1.0","model_manifest":model_manifest,"embedding":spec,"chunk_count":len(rows)})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="results/auto_update_demo")
    parser.add_argument("--cases",nargs="*")
    parser.add_argument("--modes",nargs="*",default=["baseline","hierarchical","auto"])
    parser.add_argument("--production",action="store_true")
    parser.add_argument("--index")
    args=parser.parse_args()
    output=Path(args.output).resolve(); index=output/"snapshot"
    config=load_config("configs/rag.local.json")
    config=replace(config,rerank=False,candidates=8,rerank_candidates=8,evidence_limit=5,research_evidence_per_task=5,max_answer_tokens=1000)
    if args.index:
        index=Path(args.index).resolve()
    elif args.production:
        index=Path(".local/indexes/hierarchical_20261003").resolve()
    else:
        prepare(index,config)
    cases=[
        {"id":"simple_en","question":"What is the rated voltage of M1?","filters":{"source_relative":"text_m1.png"},"expected":"24","status":"answered","routes":["baseline"]},
        {"id":"simple_vi","question":"Điện áp định mức của M1 là bao nhiêu?","filters":{"source_relative":"text_m1.png"},"expected":"24","status":"answered","routes":["baseline"]},
        {"id":"calculation","question":"Calculate the sum of the Latest Estimate costs of Alpha and Beta in GBP.","filters":{"source_relative":["alpha.png","beta.png"]},"expected":"300","status":"answered","routes":["hierarchical"]},
        {"id":"visual_simple","question":"Read the rated voltage of M1 from the image.","filters":{"source_relative":"visual_m1.png"},"expected":"24","status":"answered","routes":["baseline"]},
        {"id":"visual_calculation","question":"Read M1 and M2 rated voltages from their images and calculate M1 minus M2 in V.","filters":{"source_relative":["visual_m1.png","visual_m2.png"]},"expected":"12","status":"answered","routes":["hierarchical"]},
        {"id":"missing","question":"What is the rated voltage of MissingDevice?","filters":{"source_relative":[]},"expected":"","status":"abstain","routes":["baseline"]},
    ]
    if args.production:
        cases=[
            {"id":"corpus_budget","question":"What is the Latest Estimate cost for Jessie in GBP?","filters":{"source_relative":"nhwc0428.pdf","page_number":11,"kind":"table"},"expected":"469","status":"answered","routes":["baseline"]},
            {"id":"corpus_visual","question":"Read the university name from the image.","filters":{"source_relative":"images/24580.png"},"expected":"California","status":"answered","routes":["baseline"]},
            {"id":"corpus_bat_missing_transfer","question":"Calculate the gross margin from the illicit-trade reduction targets of Jessie and Dawn minus their Latest Estimate costs in GBP.","filters":{"source_relative":"nhwc0428.pdf"},"expected":"","status":"clarification","routes":["hierarchical"]},
        ]
    write_json(output/"cases.json",{"review_status":"agent_reviewed_source_checks" if args.production else "agent_reviewed_synthetic_sources","cases":cases,"note":"These labels are demo checks, not human-reviewed corporate holdout accuracy."})
    results=[]
    planner_outputs=[]
    visual_outputs=[]
    class RecordedLocalGenerator(Generator):
        def generate_json(self,system,prompt,*args,**kwargs):
            result=super().generate_json(system,prompt,*args,**kwargs)
            if "bounded document research plan" in system:
                planner_outputs.append(result[0])
            elif "Read only the attached source images/crops" in system:
                visual_outputs.append(result[0])
            return result
    rag_runner.Generator=RecordedLocalGenerator
    for case in cases:
        if args.cases and case["id"] not in args.cases: continue
        for mode in args.modes:
            path=output/(case["id"]+"_"+mode+".json")
            if path.is_file():
                results.append(read_json(path)); continue
            started=time.perf_counter()
            planner_outputs.clear()
            visual_outputs.clear()
            print("RUN",case["id"],mode,flush=True)
            try:
                answer=ask(index=index,question=case["question"],config=replace(config,orchestration_mode=mode),filters=case["filters"])
                checks={"status":answer["status"]==case["status"],"answer_literal_proxy":not case["expected"] or case["expected"].casefold() in answer["answer"].casefold(),
                        "route_agreement":mode!="auto" or answer["trace"]["routing"].get("final_route") in case["routes"]}
                if case["id"] in {"calculation","visual_calculation"}:
                    calculations=answer["trace"].get("calculations",[])
                    checks["calculator_exact_value"] = bool(calculations) and Decimal(calculations[-1]["value"])==Decimal(case["expected"])
                    facts=answer["trace"].get("facts",{})
                    checks["all_requested_source_facts"] = len(facts)==2
                    if case["id"]=="visual_calculation":
                        checks["image_fact_provenance"] = len(facts)==2 and all(f.get("origin")=="source_visual" and f.get("image_id") and f.get("visual_observation_id") for f in facts.values())
                passed=all(checks.values())
                record={"case_id":case["id"],"mode":mode,"passed":passed,"checks":checks,"answer":answer,"elapsed_seconds":time.perf_counter()-started}
            except Exception as exc:
                causes=[]
                cause=exc.__cause__
                while cause is not None:
                    causes.append(type(cause).__name__+": "+str(cause)); cause=cause.__cause__
                record={"case_id":case["id"],"mode":mode,"passed":False,"error":type(exc).__name__+": "+str(exc),"error_causes":causes,"elapsed_seconds":time.perf_counter()-started}
            record["planner_outputs"]=list(planner_outputs)
            record["visual_outputs"]=list(visual_outputs)
            write_json(path,record); results.append(record)
            print("RESULT",case["id"],mode,"PASS" if record["passed"] else "FAIL",round(record["elapsed_seconds"],2),record.get("error",record.get("answer",{}).get("status")),flush=True)
    grouped={}
    for mode in args.modes:
        samples=[r for r in results if r["mode"]==mode]
        latencies=sorted(r["elapsed_seconds"] for r in samples)
        grouped[mode]={"passed":sum(r["passed"] for r in samples),"count":len(samples),"p50_seconds":statistics.median(latencies) if latencies else None,"p95_seconds":latencies[min(len(latencies)-1,int(.95*len(latencies)))] if latencies else None}
    write_json(output/"summary.json",{"regime":"cold-load sequential local model stages","model":read_json(index/"manifest.json")["model_manifest"],"modes":grouped,"results":results})
    print(json.dumps(grouped),flush=True)


if __name__=="__main__": main()
