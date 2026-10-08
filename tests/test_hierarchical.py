import json
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest

from denso_a3.bt4.hierarchy import build_hierarchy, validate_hierarchy
from denso_a3.bt5.store import RetrievalStore
from denso_a3.bt5.hierarchy_store import HierarchyStore
from denso_a3.bt6.planner import plan_query
from denso_a3.bt6.task_planner import validate_research_plan
from denso_a3.bt6.hierarchical_retrieval import retrieve_hierarchical
from denso_a3.bt7.calculation import parse_quantity, calculate
from denso_a3.bt7.fact_extraction import validate_fact, quantity_candidates
from denso_a3.bt7.memory import ConversationStore
from denso_a3.contracts import RagConfig
from denso_a3.pipeline.research_runner import run_research


def test_latest_estimate_is_a_budget_column_not_current_revision():
    plan = plan_query("Gross Margin minus Latest Estimate of two projects",{"documents":{}})
    assert "current" not in plan.intents
    assert "version" not in plan.intents


def leaf(cid,did,text,page=1):
    return {"chunk_id":cid,"content_key":cid,"document_id":did,"source_relative":did+".pdf",
        "page_id":f"{did}:p{page}","page_number":page,"image_sha256":"sha","source_sha256":did,
        "kind":"text","text":text,"metadata":{},"parent_id":f"{did}:p{page}"}


def snapshot(tmp_path,rows):
    docs = [{"document_id":did,"source_relative":did+".pdf"} for did in dict.fromkeys(c["document_id"] for c in rows)]
    nodes = build_hierarchy(rows,docs)
    with RetrievalStore(tmp_path,create=True,dimension=2) as store:
        store.add(rows,np.tile(np.array([[1.,0.]],dtype=np.float32),(len(rows),1)))
        h = HierarchyStore(store,create=True)
        try:
            h.add(nodes,np.tile(np.array([[1.,0.]],dtype=np.float32),(len(nodes),1)))
        finally:
            h.close()
    (tmp_path/"assets").mkdir()
    (tmp_path/"assets/sha.png").write_bytes(b"fixture")
    return {"state":"ready","schema_version":"1.1","hierarchy_version":"1.0","model_manifest":{}}


def test_hierarchy_sources_pages_and_orphans():
    rows = [leaf("a","D1","Text"),leaf("b","D2","Other")]
    nodes = build_hierarchy(rows,[{"document_id":"D1","source_relative":"a.pdf"},{"document_id":"D2","source_relative":"b.pdf"}])
    assert len(nodes)==4
    nodes[1]["parent_node_id"] = "missing"
    with pytest.raises(ValueError,match="Orphan"):
        validate_hierarchy(nodes,rows)


def test_routing_parent_and_escape_never_remove_filters(tmp_path):
    rows = [leaf("a","D1","Jessie cost GBP469,000",1),leaf("b","D1","Secret Dawn GBP1,103,000",2),
            leaf("c","D2","Jessie cost GBP999,000",1)]
    manifest = snapshot(tmp_path,rows)
    config = RagConfig(rerank=False,orchestration_mode="hierarchical")
    with RetrievalStore(tmp_path) as store:
        h = HierarchyStore(store)
        try:
            plan = plan_query("Jessie cost",{"documents":{}},"lexical",{"document_id":"D1","page_number":1})
            hits,trace = retrieve_hierarchical(store,h,plan,config,manifest)
            assert [e.chunk["chunk_id"] for e in hits]==["a"]
            assert trace["eligible_chunks"]==1
        finally:
            h.close()


def test_cycle_and_unsupported_calculator_input_rejected():
    base = {"tasks":[{"task_id":"T1","query":"q","label":"q","entity":"","kind":"text","depends_on":["T1"]}],"calculations":[]}
    with pytest.raises(ValueError,match="Dependencies"):
        validate_research_plan(base,6)
    base["tasks"][0]["depends_on"] = []
    base["calculations"] = [{"id":"C1","label":"calc","operation":"sum","inputs":["T1","100"]}]
    with pytest.raises(ValueError,match="operands"):
        validate_research_plan(base,6)
    base["tasks"].append({"task_id":"T2","query":"cost","label":"cost","entity":"","kind":"quantity","depends_on":[]})
    base["calculations"][0]["inputs"] = ["T1","T2"]
    with pytest.raises(ValueError,match="quantities"):
        validate_research_plan(base,6)
    base["calculations"] = []
    base["tasks"][1]["query"] = "Chi phí của công ty đó"
    with pytest.raises(ValueError,match="Unresolved"):
        validate_research_plan(base,6)


def fact(task,raw,cid="a"):
    value,dims = parse_quantity(raw)
    return {"task_id":task,"value":value,"dimensions":dims,"unit":"quantity","chunk_id":cid}


def test_bat_decimal_currency_and_per_mille():
    facts = {"J":fact("J","600m sticks"),"D":fact("D","620 million sticks"),
        "R":fact("R","GBP 8.6 per mille"),"CJ":fact("CJ","GBP469,000"),"CD":fact("CD","GBP1,103,000")}
    ops = [{"id":i,"label":i,"operation":op,"inputs":inputs} for i,op,inputs in [
        ("V","sum",["J","D"]),("GM","multiply",["V","R"]),("C","sum",["CJ","CD"]),("NET","subtract",["GM","C"])]]
    result = calculate(ops,facts)
    assert result[1]["value"]=="10492000.0000"
    assert result[-1]["value"]=="8920000.0000"
    assert result[-1]["dimensions"]=={"GBP":1}
    with pytest.raises(ValueError,match="incompatible"):
        calculate([{"id":"C","label":"C","operation":"sum","inputs":["CJ","J"]}],facts)
    with pytest.raises(ValueError,match="incompatible"):
        calculate([{"id":"C","label":"C","operation":"sum","inputs":["CJ","USD"]}],{**facts,"USD":fact("USD","USD2.15m")})


def test_fact_requires_source_entity_and_literal():
    task = {"task_id":"T1","kind":"quantity","entity":"Jessie"}
    row = leaf("a","D1","Jessie Latest estimate: GBP469,000")
    output = {"found":True,"chunk_id":"a","quote":row["text"],"raw_value":"GBP469,000"}
    assert validate_fact(task,output,{"a":row})["value"]=="469000"
    with pytest.raises(ValueError,match="quote"):
        validate_fact(task,{**output,"raw_value":"GBP999,000"},{"a":row})
    with pytest.raises(ValueError,match="entity"):
        validate_fact({**task,"entity":"Dawn"},output,{"a":row})
    with pytest.raises(ValueError,match="captions"):
        validate_fact(task,output,{"a":{**row,"kind":"page_visual"}})


class ScriptedGenerator:
    def __init__(self,plan,extract):
        self.plan,self.extract = plan,extract
    def close(self):
        pass
    def count_tokens(self,*args):
        return 100
    def generate_json(self,system,prompt,**kwargs):
        if "research plan" in system:
            return self.plan,{}
        return self.extract(json.loads(prompt.split('\nPrevious')[0])),{}


def test_bridge_cross_document_and_bounded_missing(tmp_path):
    rows = [leaf("a","A","Orion manufacturer: Vega"),leaf("b","B","Vega cost: GBP100")]
    manifest = snapshot(tmp_path,rows)
    plan = {"tasks":[
        {"task_id":"T1","query":"Orion manufacturer","label":"manufacturer","entity":"Orion","kind":"text","depends_on":[]},
        {"task_id":"T2","query":"{T1} cost","label":"cost","entity":"{T1}","kind":"quantity","depends_on":["T1"]}],"calculations":[],"clarification":""}
    def extract(packet):
        task = packet["task"]["task_id"]
        if task=="T2":
            assert packet["task"]["entity"]=="Vega"
        cid,quote,raw = ("a",rows[0]["text"],"Vega") if task=="T1" else ("b",rows[1]["text"],"GBP100")
        return {"found":True,"chunk_id":cid,"quote":quote,"raw_value":raw,"missing_query":"","period":"","scenario":""}
    gen = ScriptedGenerator(plan,extract)
    # Synthesis validates citations through the normal BT7 path.
    import denso_a3.pipeline.research_runner as module
    original = module.grounded_answer
    module.grounded_answer = lambda *args,**kwargs: ({"answer":"Vega cost GBP100 [E2]","status":"answered"},[],{})
    try:
        result = run_research(tmp_path,"Orion cost?",plan_query("Orion cost?",{"documents":{}},"lexical"),
            RagConfig(rerank=False,orchestration_mode="hierarchical"),manifest,gen)
        assert result.status=="answered"
        assert len(result.trace["rounds"])==2
        assert result.trace["rounds"][1]["searches"][0]["query"]=="Vega cost"
        assert {e["chunk"]["document_id"] for e in result.evidence}=={"A","B"}
        missing = ScriptedGenerator(plan,lambda packet:{"found":False,"missing_query":packet["task"]["query"]})
        result = run_research(tmp_path,"Orion cost?",plan_query("Orion cost?",{"documents":{}},"lexical"),
            RagConfig(rerank=False,orchestration_mode="hierarchical"),manifest,missing)
        assert result.status=="abstain" and result.trace["stop_reason"]=="no_progress"
    finally:
        module.grounded_answer = original


def test_pending_persistence_and_scope(tmp_path):
    memory = ConversationStore(tmp_path/"memory.sqlite")
    cid = memory.create()
    memory.set_pending(cid,"scope1",{"scope":"scope1","original_question":"q"})
    memory.close()
    memory = ConversationStore(tmp_path/"memory.sqlite")
    assert memory.pending(cid,"scope1")["original_question"]=="q"
    assert memory.pending(cid,"scope2") is None
    memory.set_pending(cid,"scope1",None)
    assert memory.pending(cid,"scope1") is None
    memory.close()


def budget_leaf(cid,did,entity,old,latest):
    row = leaf(cid,did,f"TABLE\nRow 1: col 1 HEADER: Workstream | col 2 HEADER: Co plan | col 3 HEADER: Latest estimate\n"
               f"Row 2: col 1: {entity} | col 2: {old} | col 3: {latest}")
    row["kind"] = "table"
    row["table"] = {"cells":[
        {"row_index":0,"column_index":2,"text_raw":"Latest estimate","column_header":True},
        {"row_index":1,"column_index":0,"text_raw":entity,"column_header":False},
        {"row_index":1,"column_index":1,"text_raw":old,"column_header":False},
        {"row_index":1,"column_index":2,"text_raw":latest,"column_header":False}]}
    return row


def test_source_rules_bind_target_section_row_and_column():
    rows = {"a":leaf("a","A","Regional aim is 1.5bn sticks.\nProject Alpha\n"
        "The measured base was 5.7bn sticks.\nA reduction will account for 600m sticks.\n"
        "Project Beta\nThe target is 999m sticks."),
        "b":budget_leaf("b","B","Alpha","GBP700","GBP900 (USD1500)")}
    volume = {"task_id":"T1","query":"Alpha target volume sticks","label":"Volume","entity":"Alpha",
              "kind":"quantity","expected_unit":"stick"}
    found = quantity_candidates(volume,rows)
    assert [f["value"] for f in found]==["600000000"]
    assert found[0]["quote"].startswith("Project Alpha") and "Project Beta" not in found[0]["quote"]
    cost = {**volume,"task_id":"T2","query":"Alpha Latest Estimate cost","expected_unit":"GBP"}
    assert [f["value"] for f in quantity_candidates(cost,rows)]==["900"]
    with pytest.raises(ValueError,match="row and Latest"):
        validate_fact(cost,{"found":True,"chunk_id":"b","quote":rows["b"]["text"],"raw_value":"GBP700"},rows)
    unknown = {**rows["b"],"table":{"cells":[{**c,"content_status":"unknown"} for c in rows["b"]["table"]["cells"]]}}
    assert not quantity_candidates(cost,{"b":unknown})
    ambiguous = budget_leaf("b","B","Alpha","GBP700","GBP900monthly")
    assert not quantity_candidates(cost,{"b":ambiguous})


def test_five_inputs_across_files_and_candidate_conflict(tmp_path):
    rows = [leaf("a","A","Project Alpha\nA reduction accounts for 600m sticks."),
            leaf("b","B","Project Beta\nThe target reduction is 620 million sticks."),
            leaf("r","C","On average gross margin is GBP 8.6 per mille."),
            budget_leaf("ca","D","Alpha","GBP469,000","GBP469,000"),
            budget_leaf("cb","E","Beta","GBP1,103,000","GBP1,103,000 (USD2.15m)")]
    manifest = snapshot(tmp_path,rows)
    tasks = [{"task_id":f"T{i}","query":q,"label":q,"entity":entity,"kind":"quantity",
              "expected_unit":unit,"depends_on":[]} for i,q,entity,unit in [
        (1,"Alpha target sticks","Alpha","stick"),(2,"Beta target sticks","Beta","stick"),
        (3,"Region gross margin per mille","","GBP/stick"),(4,"Alpha Latest Estimate cost","Alpha","GBP"),
        (5,"Beta Latest Estimate cost","Beta","GBP")]]
    operations = [{"id":i,"label":i,"operation":op,"inputs":inputs} for i,op,inputs in [
        ("C1","sum",["T1","T2"]),("C2","multiply",["C1","U1"]),
        ("C3","multiply",["C2","T3"]),("C4","sum",["T4","T5"]),
        ("C5","subtract",["C3","C4"])]]
    plan = {"tasks":tasks,"calculations":operations,"clarification":""}
    gen = ScriptedGenerator(plan,lambda p: pytest.fail("All five literals are source-extracted"))
    config = RagConfig(rerank=False,orchestration_mode="hierarchical")
    question = "Margin for Project Alpha and Project Beta, assuming 100% of reduction converts into sales?"
    result = run_research(tmp_path,question,plan_query(question,{"documents":{}},"lexical"),config,manifest,gen)
    assert result.status=="answered" and result.trace["coverage"]["ready"]
    assert float(result.trace["calculations"][-1]["value"])==8920000
    assert result.trace["calculations"][-1]["condition_ids"]==["U1"]
    assert {e["chunk"]["document_id"] for e in result.evidence}=={"A","B","C","D","E"}
    assert len(result.citations)==5
    assert all(c["chunk_id"] in {r["chunk_id"] for r in rows} for c in result.citations)
    # A second source for the same requested cell cannot be silently selected by ranking.
    other = budget_leaf("cx","F","Alpha","GBP200","GBP999")
    with RetrievalStore(tmp_path) as store:
        store.add([other],np.array([[1.,0.]],dtype=np.float32))
    result = run_research(tmp_path,question,plan_query(question,{"documents":{}},"lexical"),config,manifest,gen)
    assert result.status=="conflict" and result.trace["stop_reason"]=="conflict"
    assert {"ca","cx"}.issubset({c["chunk_id"] for c in result.citations})


def test_call_budget_does_not_claim_complete_answer(tmp_path):
    rows = [leaf("a","A","Orion manufacturer Vega")]
    manifest = snapshot(tmp_path,rows)
    tasks = [{"task_id":f"T{i}","query":f"missing {i}","label":str(i),"entity":"","kind":"text","depends_on":[]} for i in (1,2)]
    gen = ScriptedGenerator({"tasks":tasks,"calculations":[],"clarification":""},
        lambda p:{"found":False,"missing_query":"new query"})
    result = run_research(tmp_path,"Unknown?",plan_query("Unknown?",{"documents":{}},"lexical"),
        RagConfig(rerank=False,orchestration_mode="hierarchical",research_max_calls=1),manifest,gen)
    assert result.status=="abstain" and result.trace["retrieval_calls"]==1
    assert result.trace["stop_reason"]=="call_budget"


def test_baseline_context_budget_remains_compatible():
    RagConfig(context_tokens=2048).validate()
    with pytest.raises(ValueError,match="Research context"):
        RagConfig(context_tokens=2048,orchestration_mode="hierarchical").validate()


def test_planner_and_synthesis_context_guard(tmp_path,monkeypatch):
    from denso_a3.bt6.task_planner import build_research_plan
    from denso_a3.contracts import ContextBudgetExceeded
    config = RagConfig(rerank=False,orchestration_mode="hierarchical")
    oversized = ScriptedGenerator({},lambda p:{})
    oversized.count_tokens = lambda *args: 10000
    with pytest.raises(ContextBudgetExceeded):
        build_research_plan("Question", "en", oversized, config)
    rows = [leaf("a","A","Orion manufacturer: Vega")]
    manifest = snapshot(tmp_path,rows)
    plan = {"tasks":[{"task_id":"T1","query":"Orion manufacturer","label":"Maker","entity":"Orion",
        "kind":"text","depends_on":[]}],"calculations":[],"clarification":""}
    gen = ScriptedGenerator(plan,lambda p:{"found":True,"chunk_id":"a","quote":rows[0]["text"],"raw_value":"Vega"})
    def over_budget(*args,**kwargs):
        assert kwargs["max_context_tokens"]==4500
        raise ContextBudgetExceeded("Required evidence exceeds budget")
    monkeypatch.setattr("denso_a3.pipeline.research_runner.grounded_answer",over_budget)
    result = run_research(tmp_path,"Orion maker?",plan_query("Orion maker?",{"documents":{}},"lexical"),config,manifest,gen)
    assert result.status=="abstain" and result.trace["stop_reason"]=="context_budget"


def test_hierarchy_metadata_snapshot_uses_current_leaf_eligibility(tmp_path):
    from denso_a3.bt4.metadata import create_registry
    from denso_a3.pipeline.index_builder import update_metadata
    old,new = tmp_path/"old",tmp_path/"new"
    old.mkdir()
    rows = [leaf("a","A","Orion budget"),leaf("b","B","Vega budget")]
    manifest = snapshot(old,rows)
    docs = [{"document_id":did,"source_relative":did+".pdf","source_sha256":did} for did in ("A","B")]
    registry = create_registry(docs)
    registry["documents"]["A"]["fields"]["revision_id"] = {"value":"R2","origin":"user_form","review_status":"user_verified"}
    (old/"manifest.json").write_text(json.dumps(manifest))
    (old/"documents.json").write_text(json.dumps(docs))
    update_metadata(old,registry,new)
    with RetrievalStore(new) as store:
        h = HierarchyStore(store)
        try:
            allowed = store.allowed({"revision_id":"R2"})
            assert allowed==["a"]
            selected = h.rank("budget",allowed,"document")
            assert {h.nodes[n]["document_id"] for n,_ in selected}=={"A"}
        finally:
            h.close()
    with RetrievalStore(old) as store:
        assert store.allowed({"revision_id":"R2"})==[]


def test_multidoc_score_rejects_missing_evidence_and_citations():
    from denso_a3.evaluation.multidoc_evaluator import score_record
    answer = {"status":"answered","evidence":[{"chunk":{"chunk_id":"a"}}],"citations":[],
              "trace":{"calculations":[{"id":"C1","value":"1.00"}]}}
    score = score_record(answer,{"status":"answered","evidence_sets":[["a","b"]],"calculations":{"C1":1}})
    assert not score["required_evidence_set_recall"] and not score["citation_pointer_valid"]
    assert score["calculation_correct"]


def test_research_synthesis_requires_each_source_inline(tmp_path):
    from denso_a3.bt7.answering import grounded_answer
    from denso_a3.contracts import RetrievedEvidence
    (tmp_path/"assets").mkdir()
    (tmp_path/"assets/sha.png").write_bytes(b"fixture")
    evidence = [RetrievedEvidence(leaf("a","A","Orion maker Vega"),1,evidence_id="E1"),
                RetrievedEvidence(leaf("b","B","Vega cost GBP100"),1,evidence_id="E2")]
    class Generator:
        calls = 0
        def generate_json(self,*args,**kwargs):
            self.calls += 1
            return {"status":"answered","claims":[{"text":"Orion maker Vega","evidence_ids":["E1"]}]+
                ([{"text":"Vega cost GBP100","evidence_ids":["E2"]}] if self.calls>1 else [])},{"cost_usd":0}
    gen = Generator()
    result,_,_ = grounded_answer("Maker and cost?","en",evidence,tmp_path,gen,required_ids=["E1","E2"])
    assert gen.calls==2 and "[E2]" in result["answer"]


def test_research_reuses_validated_plan_without_planner_model_call(tmp_path):
    rows = [leaf("a","A","Project Alpha\nThe target reduction is 600m sticks.")]
    manifest = snapshot(tmp_path,rows)
    plan = validate_research_plan({"tasks":[{"task_id":"T1","query":"Alpha target reduction sticks",
        "label":"Volume","entity":"Alpha","kind":"quantity","expected_unit":"stick","depends_on":[]}],
        "calculations":[],"clarification":""},6,question="Alpha target?")
    gen = ScriptedGenerator({},lambda packet: pytest.fail("Sourced numeric fact needs no model extraction"))
    import denso_a3.pipeline.research_runner as module
    original = module.grounded_answer
    module.grounded_answer = lambda *args,**kwargs: ({"answer":"Alpha 600m sticks [E1]","status":"answered"},[],{})
    try:
        result = run_research(tmp_path,"Alpha target?",plan_query("Alpha target?",{"documents":{}},"lexical"),
            RagConfig(rerank=False),manifest,gen,research_plan=plan)
        assert result.status=="answered" and result.trace["planner_reused"]
        assert result.trace["budget"]["planner_calls"]==0
        assert result.timings_ms["planning"]==0
    finally:
        module.grounded_answer = original


def test_research_after_baseline_has_no_new_retrieval_budget(tmp_path):
    from denso_a3.pipeline.query_budget import QueryBudget
    rows = [leaf("a","A","Project Alpha\nThe target reduction is 600m sticks.")]
    manifest = snapshot(tmp_path,rows)
    config = RagConfig(rerank=False,query_max_retrieval_calls=1)
    budget = QueryBudget(config)
    budget.consume_retrieval()  # The baseline attempt already spent this call.
    plan = {"tasks":[{"task_id":"T1","query":"Alpha target reduction sticks","label":"Volume",
        "entity":"Alpha","kind":"quantity","expected_unit":"stick","depends_on":[]}],"calculations":[]}
    result = run_research(tmp_path,"Alpha target?",plan_query("Alpha target?",{"documents":{}},"lexical"),
        config,manifest,ScriptedGenerator({},lambda packet: pytest.fail("No generation permitted")),
        research_plan=plan,budget=budget)
    assert result.status=="abstain" and result.trace["retrieval_calls"]==0
    assert result.trace["budget"]["retrieval_calls"]==1
    assert result.trace["stop_reason"]=="retrieval_calls_budget"


def test_invalid_reused_plan_rejected_before_opening_retrieval(tmp_path):
    invalid = {"tasks":[{"task_id":"T1","query":"q","label":"q","depends_on":["T1"]}],"calculations":[]}
    with pytest.raises(ValueError,match="Dependencies"):
        run_research(tmp_path,"q",plan_query("q",{"documents":{}},"lexical"),RagConfig(),{},
            ScriptedGenerator({},lambda packet: pytest.fail("No model call permitted")),research_plan=invalid)
