import json
from dataclasses import replace
from PIL import Image
import numpy as np
from denso_a3.bt7.answering import grounded_answer
from denso_a3.bt5.store import RetrievalStore
from denso_a3.contracts import RetrievedEvidence, QueryRequirement, RagConfig, QueryBudgetExceeded
from denso_a3.pipeline import rag_runner


def test_claim_cannot_invent_number_with_valid_quote(tmp_path):
    (tmp_path/"assets").mkdir()
    Image.new("RGB",(20,20)).save(tmp_path/"assets/sha.png")
    chunk={"chunk_id":"a","kind":"text","text":"M1 voltage: 24 V","image_sha256":"sha","source_relative":"m1.png","page_id":"p","page_number":None}
    class Model:
        calls=0
        def generate_json(self,*args,**kwargs):
            self.calls+=1
            refs=[{"source_id":"E1","quote":chunk["text"]}]
            return {"status":"answered","claims":[{"text":"M1 voltage: "+("999" if self.calls==1 else "24")+" V","support_refs":refs}],
                    "coverage":[{"requirement_id":"R1","state":"supported","support_refs":refs}]},{}
    model=Model()
    result,citations,_=grounded_answer("M1 voltage?","en",[RetrievedEvidence(chunk,1,evidence_id="E1")],tmp_path,model,
                                      requirements=[QueryRequirement("R1","voltage",entity="M1",attribute="voltage")])
    assert model.calls==2 and "999" not in result["answer"] and "24" in result["answer"]
    assert citations[0]["chunk_id"]=="a"


def test_baseline_visual_budget_exit_retains_attempt_and_evidence(tmp_path,monkeypatch):
    chunk={"chunk_id":"a","content_key":"a","document_id":"m1","kind":"text","text":"M1 voltage: 24 V",
           "image_sha256":"sha","source_relative":"m1.png","page_id":"p","page_number":None,"metadata":{}}
    (tmp_path/"assets").mkdir()
    Image.new("RGB",(20,20)).save(tmp_path/"assets/sha.png")
    with RetrievalStore(tmp_path,create=True,dimension=2) as store:
        store.add([chunk],np.array([[1.,0.]],dtype=np.float32))
    (tmp_path/"manifest.json").write_text(json.dumps({"state":"ready","schema_version":"1.0","model_manifest":{}}))
    (tmp_path/"registry.json").write_text(json.dumps({"documents":{}}))
    def exhausted(*args,**kwargs):
        raise QueryBudgetExceeded("visual_calls_budget")
    monkeypatch.setattr("denso_a3.pipeline.baseline_runner.run_visual_phase",exhausted)
    result=rag_runner.ask(index=tmp_path,question="M1 voltage?",config=RagConfig(rerank=False),strategy="lexical")
    assert result["status"]=="abstain" and result["trace"]["stop_reason"]=="visual_calls_budget"
    assert result["evidence"][0]["chunk"]["chunk_id"]=="a" and result["citations"][0]["chunk_id"]=="a"
    assert result["trace"]["attempts"][0]["chunk_ids"]==["a"]
    assert result["trace"]["budget"]["escalations"]==0


def test_latest_estimate_table_hint_uses_readable_requested_cell_with_unknown_other_column():
    from denso_a3.bt7.answering import support_sources
    question="What is the Latest Estimate cost for Alpha in GBP?"
    text="Row 1: Project | Co plan | Latest Estimate | Variance\nRow 2: Alpha | GBP700 | GBP900 | UNKNOWN\nRow 3: Beta | GBP200 | GBP300 | UNKNOWN"
    cells=[{"row_index":r,"column_index":c,"text_raw":value,"column_header":r==0,
            "content_status":"unknown" if value=="UNKNOWN" else "observed"}
           for r,row in enumerate([["Project","Co plan","Latest Estimate","Variance"],
                                   ["Alpha","GBP700","GBP900","UNKNOWN"],["Beta","GBP200","GBP300","UNKNOWN"]])
           for c,value in enumerate(row)]
    chunk={"chunk_id":"a","kind":"table","text":text,"table":{"cells":cells}}
    sources=support_sources([RetrievedEvidence(chunk,1,evidence_id="E1")],None,
        [QueryRequirement("R1","cost",entity="Alpha",attribute="cost")],question)
    hints=sources["E1"]["validated_table_facts"]
    assert len(hints)==1 and hints[0]["raw_value"]=="GBP900" and hints[0]["origin"]=="source_rule"
    assert hints[0]["quote"]==text.splitlines()[1]
    both=support_sources([RetrievedEvidence(chunk,1,evidence_id="E1")],None,
        [QueryRequirement("R1","cost",entity="Alpha",attribute="cost"),
         QueryRequirement("R2","cost",entity="Beta",attribute="cost")],question)["E1"]["validated_table_facts"]
    assert [(h["requirement_id"],h["raw_value"]) for h in both]==[("R1","GBP900"),("R2","GBP300")]
    # Unreadable requested cell must not gain a fabricated hint.
    cells[6]["content_status"]="unknown"
    assert "validated_table_facts" not in support_sources([RetrievedEvidence(chunk,1,evidence_id="E1")],None,
        [QueryRequirement("R1","cost",entity="Alpha",attribute="cost")],question)["E1"]
