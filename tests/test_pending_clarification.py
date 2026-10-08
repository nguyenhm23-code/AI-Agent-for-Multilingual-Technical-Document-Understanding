"""Dispatcher clarification demos using actual retrieval and source rules."""
import json
import numpy as np
import pytest

from denso_a3.bt4.hierarchy import build_hierarchy
from denso_a3.bt5.store import RetrievalStore
from denso_a3.bt5.hierarchy_store import HierarchyStore
from denso_a3.bt7.memory import ConversationStore
from denso_a3.contracts import RagConfig
from denso_a3.pipeline import rag_runner


def build_index(root):
    rows = [{"chunk_id":cid,"content_key":cid,"document_id":did,"source_relative":did+".pdf",
        "page_id":did+":p1","page_number":1,"image_sha256":"sha","source_sha256":did,
        "kind":"text","text":text,"metadata":{},"parent_id":did+":p1"} for cid,did,text in [
        ("volume","A","Project Alpha\nThe target reduction is 600m sticks."),
        ("rate","B","Regional gross margin is GBP 8.6 per mille.")]]
    docs = [{"document_id":did,"source_relative":did+".pdf"} for did in ("A","B")]
    nodes = build_hierarchy(rows,docs)
    with RetrievalStore(root,create=True,dimension=2) as store:
        store.add(rows,np.tile(np.array([[1.,0.]],dtype=np.float32),(len(rows),1)))
        hierarchy = HierarchyStore(store,create=True)
        hierarchy.add(nodes,np.tile(np.array([[1.,0.]],dtype=np.float32),(len(nodes),1)))
        hierarchy.close()
    (root/"assets").mkdir()
    (root/"assets/sha.png").write_bytes(b"fixture")
    (root/"manifest.json").write_text(json.dumps({"state":"ready","schema_version":"1.1","hierarchy_version":"1.0","model_manifest":{}}))
    (root/"registry.json").write_text(json.dumps({"documents":{}}))


class ClarificationGenerator:
    instances = []
    def __init__(self,*args):
        self.calls = []
        type(self).instances.append(self)
    def close(self):
        pass
    def count_tokens(self,*args):
        return 100
    def generate_json(self,system,prompt,**kwargs):
        self.calls.append(system)
        if "independent answer verifier" in system:
            packet = json.loads(prompt)
            sources = {s["source_id"]: s for s in packet["sources"]}
            return {"question_supported": True, "reason": "Scripted source/condition verification",
                "claims": [{"claim_id": c["claim_id"], "verdict": "supported", "reason": "Fixture support",
                    "support_refs": [{"source_id": c["allowed_source_ids"][-1],
                                      "quote": sources[c["allowed_source_ids"][-1]]["text"]}]} for c in packet["claims"]]}, {}
        assert "research plan" in system  # Pending resolution does not rewrite old answers.
        packet = json.loads(prompt.split("\nValidation")[0])
        if not packet["user_clarification"]:
            result = {"tasks":[],"calculations":[],"question_conditions":[],
                      "clarification":"What percentage of reduction converts into sales?"}
        else:
            assert packet["user_clarification"]=="Assume 50% of reduction converts into sales"
            result = {"tasks":[
                {"task_id":"T1","query":"Alpha target reduction sticks","label":"Reduction","entity":"Alpha",
                 "kind":"quantity","expected_unit":"stick","depends_on":[]},
                {"task_id":"T2","query":"Regional gross margin per mille","label":"Margin rate","entity":"",
                 "kind":"quantity","expected_unit":"GBP/stick","depends_on":[]}],
                "calculations":[{"id":"C1","label":"Sales","operation":"multiply","inputs":["T1","U1"]},
                                {"id":"C2","label":"Gross margin","operation":"multiply","inputs":["C1","T2"]}],
                "question_conditions":[],"clarification":""}
        self.query_budget.consume_generation()
        self.query_budget.add_usage({"total_tokens":10})
        return result,{"total_tokens":10}


@pytest.mark.parametrize("memory_mode",["instant","long_term"])
def test_clarification_condition_actual_literal_new_retrieval_no_old_facts(tmp_path,monkeypatch,memory_mode):
    build_index(tmp_path)
    ClarificationGenerator.instances = []
    monkeypatch.setattr(rag_runner,"Generator",ClarificationGenerator)
    config = RagConfig(rerank=False,retrieval_strategy="lexical")
    options = {}
    path = tmp_path/"memory.sqlite"
    if memory_mode=="long_term":
        memory = ConversationStore(path)
        cid = memory.create()
        memory.close()
        options.update(conversation_id=cid,memory_path=path)
    first = rag_runner.ask(index=tmp_path,question="Calculate gross margin of Project Alpha",config=config,**options)
    assert first["status"]=="clarification"
    pending = first["trace"]["pending"]
    assert pending["schema_version"]=="2.0" and not pending["condition_spans"]
    # Legacy facts in a caller-supplied pending object must never become evidence.
    pending["facts"] = {"T1":{"value":"999999999999","chunk_id":"fake"}}
    pending["research_plan"] = {"tasks":[],"calculations":[]}
    if memory_mode=="instant":
        options["pending"] = pending
    else:
        memory = ConversationStore(path)
        memory.set_pending(cid,pending["scope_fingerprint"],pending)
        memory.close()
    reply = "Assume 50% of reduction converts into sales"
    result = rag_runner.ask(index=tmp_path,question=reply,config=config,**options)
    assert result["status"]=="answered"
    condition = result["trace"]["question_conditions"][0]
    assert condition["origin"]=="user_clarification" and condition["source_text"]==reply
    assert condition["raw_text"]==reply and reply[condition["start"]:condition["end"]]==reply
    assert float(result["trace"]["calculations"][-1]["value"])==2580000
    assert result["trace"]["calculations"][-1]["condition_ids"]==["U1"]
    assert result["trace"]["calculations"][-1]["sources"]==["volume","rate"]
    assert {c["chunk_id"] for c in result["citations"]}=={"volume","rate"}
    assert not any(c.get("chunk_id")=="fake" for c in result["citations"])
    assert result["trace"]["budget"]["retrieval_calls"]==2
    assert len(ClarificationGenerator.instances)==2
    assert [len(g.calls) for g in ClarificationGenerator.instances]==[1,2]
    assert result["verification"]["state"] == "verified"
    if memory_mode=="long_term":
        memory = ConversationStore(path)
        assert len(memory.history(cid))==2 and memory.pending(cid,None) is None
        memory.close()


def test_scope_changed_long_term_pending_invalidates_but_history_survives(tmp_path,monkeypatch):
    build_index(tmp_path)
    monkeypatch.setattr(rag_runner,"Generator",ClarificationGenerator)
    config = RagConfig(rerank=False,retrieval_strategy="lexical")
    path = tmp_path/"memory.sqlite"
    memory = ConversationStore(path)
    cid = memory.create()
    memory.close()
    first = rag_runner.ask(index=tmp_path,question="Calculate gross margin of Project Alpha",config=config,
                           conversation_id=cid,memory_path=path)
    assert first["status"]=="clarification"
    result = rag_runner.ask(index=tmp_path,question="Assume 50% of reduction converts into sales",config=config,
                            filters={"document_id":"A"},conversation_id=cid,memory_path=path)
    assert result["status"]=="clarification" and result["trace"]["pending_invalidated"]
    assert result["trace"]["budget"]["generation_attempts"]==0
    memory = ConversationStore(path)
    assert len(memory.history(cid))==2 and memory.pending(cid,None) is None
    memory.close()


def test_multiple_clarification_turns_preserve_literal_condition_until_units_chosen(tmp_path,monkeypatch):
    build_index(tmp_path)
    class MultipleChoices(ClarificationGenerator):
        def generate_json(self,system,prompt,**kwargs):
            if "independent answer verifier" in system:
                return super().generate_json(system,prompt,**kwargs)
            packet = json.loads(prompt.split("\nValidation")[0])
            clarification = packet["user_clarification"]
            if clarification=="Assume 50% of reduction converts into sales":
                self.calls.append(system)
                self.query_budget.consume_generation()
                return {"tasks":[],"calculations":[],"question_conditions":[],
                        "clarification":"Which currency should be used?"},{}
            if clarification.endswith("\nUse GBP"):
                assert clarification=="Assume 50% of reduction converts into sales\nUse GBP"
                packet["user_clarification"] = "Assume 50% of reduction converts into sales"
                prompt = json.dumps(packet)
            return super().generate_json(system,prompt,**kwargs)
    monkeypatch.setattr(rag_runner,"Generator",MultipleChoices)
    config = RagConfig(rerank=False,retrieval_strategy="lexical")
    first = rag_runner.ask(index=tmp_path,question="Calculate gross margin of Project Alpha",config=config)
    second = rag_runner.ask(index=tmp_path,question="Assume 50% of reduction converts into sales",config=config,
                            pending=first["trace"]["pending"])
    assert second["status"]=="clarification"
    assert second["trace"]["pending"]["user_clarification"]=="Assume 50% of reduction converts into sales"
    result = rag_runner.ask(index=tmp_path,question="Use GBP",config=config,pending=second["trace"]["pending"])
    assert result["status"]=="answered"
    row = result["trace"]["question_conditions"][0]
    assert row["origin"]=="user_clarification" and row["value"]=="0.5"
    assert row["source_text"]=="Assume 50% of reduction converts into sales\nUse GBP"
    assert row["source_text"][row["start"]:row["end"]]=="Assume 50% of reduction converts into sales"
    assert float(result["trace"]["calculations"][-1]["value"])==2580000
