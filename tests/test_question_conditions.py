"""Literal scenario provenance and rejection of model-generated conditions."""
from decimal import Decimal
import pytest

from denso_a3.bt6.question_conditions import validate_question_conditions, condition_candidates
from denso_a3.bt6.task_planner import validate_research_plan, build_research_plan
from denso_a3.bt7.calculation import calculate, parse_quantity
from denso_a3.contracts import RagConfig
from denso_a3.pipeline.query_budget import QueryBudget


def task(tid="T1", query="Project Alpha target reduction sticks", unit="stick"):
    return {"task_id":tid,"query":query,"label":query,"entity":"Alpha","kind":"quantity",
            "expected_unit":unit,"depends_on":[]}


def condition(question, **changes):
    return {"condition_id":"U1","kind":"ratio","raw_text":question,"origin":"user_question",
            "applies_to":question, **changes}


def test_literal_percentage_span_and_separate_provenance():
    question = "Assume 50% of Alpha reduction converts into sales"
    conditions = validate_question_conditions(question,None,[condition(question)])
    assert conditions[0]["start"]==0 and conditions[0]["end"]==len(question)
    assert conditions[0]["value"]=="0.5" and conditions[0]["dimensions"]=={}
    assert conditions[0]["conversion_trace"][0]["divisor"]=="100"
    operations = [{"id":"C1","label":"Sales volume","operation":"multiply","inputs":["T1","U1"]}]
    plan = validate_research_plan({"tasks":[task()],"calculations":operations},6,question=question,question_conditions=conditions)
    facts = {"T1":{"value":"600","dimensions":{"stick":1},"unit":"stick","chunk_id":"source-a"}}
    results = calculate(operations,facts,conditions,validated_plan=plan)
    assert Decimal(results[0]["value"])==Decimal(300)
    assert results[0]["sources"]==["source-a"] and results[0]["condition_ids"]==["U1"]
    assert "chunk_id" not in conditions[0]
    with pytest.raises(ValueError,match="validated research plan"):
        calculate(operations,facts,conditions)


def test_clarification_conditions_refer_to_actual_clarification():
    clarification = "Chuyển 100% phần giảm thành doanh số"
    row = condition(clarification,origin="user_clarification")
    normalized = validate_question_conditions("Tính lợi nhuận",clarification,[row])[0]
    assert normalized["origin"]=="user_clarification" and normalized["source_text"]==clarification
    with pytest.raises(ValueError,match="wording"):
        validate_question_conditions("Tính lợi nhuận",None,[row])


def test_forged_span_value_and_relation_rejected():
    question = "Hoàn thành 100% mục tiêu"
    for candidate in [condition("Giả sử chuyển 100% sang doanh số"),
                      condition(question,start=1,end=len(question)),
                      condition(question,value="0.5"),
                      condition(question,applies_to="sales transfer")]:
        with pytest.raises(ValueError):
            validate_question_conditions(question,None,[candidate])
    actual = validate_question_conditions(question,None,[condition(question)])[0]
    assert actual["applies_to"]==question
    assert "sales" not in actual["applies_to"]


def test_repeated_literal_requires_explicit_span_and_unsupported_prose_rejected():
    question = "Alpha 50%; Beta 50%"
    candidate = condition("50%")
    with pytest.raises(ValueError,match="repeated"):
        validate_question_conditions(question,None,[candidate])
    candidate.update(start=6,end=9)
    assert validate_question_conditions(question,None,[candidate])[0]["start"]==6
    with pytest.raises(ValueError,match="literal"):
        validate_question_conditions("cost approximately 5 GBP",None,[condition("cost approximately 5 GBP",kind="quantity")])


def test_task_calculation_and_condition_id_namespaces_and_dag():
    question = "Convert 50% of Alpha reduction into sales"
    base = {"tasks":[task()],"calculations":[{"id":"C1","label":"x","operation":"multiply","inputs":["T1","U1"]}],
            "question_conditions":[condition(question)]}
    assert validate_research_plan(base,6,question=question)["_validated"]
    with pytest.raises(ValueError,match="task ID"):
        validate_research_plan({**base,"tasks":[task("C1")]},6,question=question)
    with pytest.raises(ValueError,match="calculation ID"):
        validate_research_plan({**base,"calculations":[{**base["calculations"][0],"id":"T2"}]},6,question=question)
    with pytest.raises(ValueError,match="operands"):
        validate_research_plan({**base,"calculations":[{**base["calculations"][0],"inputs":["T1","C2"]}]},6,question=question)
    with pytest.raises(ValueError,match="U1"):
        validate_research_plan({**base,"question_conditions":[condition(question,condition_id="T2")]},6,question=question)


def test_gross_margin_needs_sales_relationship_even_with_all_units():
    question = "Gross margin of Project Alpha minus cost?"
    tasks = [task(),task("T2","Regional gross margin per mille","GBP/stick")]
    operations = [{"id":"C1","label":"Gross margin","operation":"multiply","inputs":["T1","T2"]}]
    with pytest.raises(ValueError,match="sales-transfer"):
        validate_research_plan({"tasks":tasks,"calculations":operations},6,question=question)
    target_only = "Gross margin of Project Alpha when we complete 100% of target?"
    with pytest.raises(ValueError,match="sales-transfer"):
        validate_research_plan({"tasks":tasks,"calculations":operations,
            "question_conditions":condition_candidates(target_only)},6,question=target_only)
    explicit = "Gross margin of Project Alpha, assume 50% of reduction converts into sales?"
    operations = [{"id":"C1","label":"Sales","operation":"multiply","inputs":["T1","U1"]},
                  {"id":"C2","label":"Margin","operation":"multiply","inputs":["C1","T2"]}]
    plan = validate_research_plan({"tasks":tasks,"calculations":operations,
        "question_conditions":condition_candidates(explicit)},6,question=explicit)
    assert plan["question_conditions"][0]["value"]=="0.5"


def test_source_task_can_establish_sales_transfer_instead_of_condition():
    tasks = [task(),task("T2","Regional gross margin per mille","GBP/stick"),
             task("T3","Project Alpha sales transfer conversion factor","number")]
    operations = [{"id":"C1","label":"Sales","operation":"multiply","inputs":["T1","T3"]},
                  {"id":"C2","label":"Margin","operation":"multiply","inputs":["C1","T2"]}]
    assert validate_research_plan({"tasks":tasks,"calculations":operations},6,
                                 question="Gross margin for Project Alpha?")["_validated"]


def test_local_planner_repair_rejects_model_invented_condition_and_counts_budget():
    question = "Find Alpha cost"
    good = {"tasks":[task(query="Alpha cost",unit="GBP")],"calculations":[],"clarification":"","question_conditions":[]}
    invented = {**good,"question_conditions":[condition("Assume 100% transfers to sales")]}
    class Generator:
        calls = 0
        def count_tokens(self,*args):
            return 100
        def generate_json(self,system,prompt,**kwargs):
            assert "user_assumptions" not in prompt
            self.calls += 1
            return invented if self.calls==1 else good,{"total_tokens":4}
    config, generator = RagConfig(),Generator()
    budget = QueryBudget(config)
    budget.active_stage = "router"
    plan,usage = build_research_plan(question,"en",generator,config,budget=budget)
    assert plan["question_conditions"]==[] and generator.calls==2
    assert budget.router_calls==2 and budget.planner_calls==0 and usage["total_tokens"]==8


@pytest.mark.parametrize("literal,value,dimensions",[
    ("5 mm","5",{"mm":1}),("5 MPa","5000000",{"Pa":1}),
    ("5 m","5000",{"mm":1}),("600m sticks","600000000",{"stick":1}),
    ("GBP5m","5000000",{"GBP":1})])
def test_quantity_scale_never_consumes_physical_unit_prefix(literal,value,dimensions):
    assert parse_quantity(literal)==(value,dimensions)


def test_planner_repairs_malformed_json_once_within_shared_budget():
    class Generator:
        calls = 0
        def count_tokens(self,*args):
            return 100
        def generate_json(self,*args,**kwargs):
            self.calls += 1
            if self.calls==1:
                raise ValueError("Model did not return a JSON object")
            return {"tasks":[task(query="Alpha cost",unit="GBP")],"calculations":[],"clarification":""},{}
    config,generator = RagConfig(),Generator()
    budget = QueryBudget(config)
    result,_ = build_research_plan("Alpha cost?","en",generator,config,budget=budget)
    assert result["_validated"] and generator.calls==2 and budget.planner_calls==2


def test_completion_percentage_does_not_become_transfer_when_sales_are_also_mentioned():
    question = "Complete 100% of Alpha target and find how reduction converts into sales"
    tasks = [task(),task("T2","Regional gross margin per mille","GBP/stick")]
    operations = [{"id":"C1","label":"Transfer","operation":"multiply","inputs":["T1","U1"]},
                  {"id":"C2","label":"Margin","operation":"multiply","inputs":["C1","T2"]}]
    with pytest.raises(ValueError,match="sales-transfer"):
        validate_research_plan({"tasks":tasks,"calculations":operations,
            "question_conditions":condition_candidates(question)},6,question=question)


@pytest.mark.parametrize("question",["Calculate Alpha total cost","Sum Alpha costs","Tính tổng chi phí Alpha"])
def test_requested_arithmetic_cannot_pass_with_no_calculations(question):
    invalid = {"tasks":[task(query="Alpha cost",unit="GBP")],"calculations":[],"clarification":""}
    valid = {**invalid,"tasks":[task(query="Alpha first cost",unit="GBP"),task("T2","Alpha second cost","GBP")],
             "calculations":[{"id":"C1","label":"Total","operation":"sum","inputs":["T1","T2"]}]}
    class Generator:
        calls = 0
        def count_tokens(self,*args): return 100
        def generate_json(self,system,prompt,**kwargs):
            assert "Device Atlas" not in prompt and "format_example" not in prompt
            self.calls += 1
            return invalid if self.calls==1 else valid,{}
    generator = Generator()
    plan,_ = build_research_plan(question,"en",generator,RagConfig())
    assert generator.calls==2 and plan["calculations"]


def test_unrequested_example_entity_rejected_and_dependency_placeholder_allowed():
    copied = {"tasks":[{**task(),"query":"Device Atlas manufacturer","entity":"Atlas"}],"calculations":[]}
    with pytest.raises(ValueError,match="entity is not stated"):
        validate_research_plan(copied,6,question="Alpha latest estimate cost")
    bridge = {"tasks":[{"task_id":"T1","query":"Alpha manufacturer","label":"Manufacturer","entity":"Alpha","kind":"text","depends_on":[]},
                        {"task_id":"T2","query":"{T1} cost","label":"Cost","entity":"{T1}","kind":"quantity","depends_on":["T1"]}],"calculations":[]}
    assert validate_research_plan(bridge,6,question="Alpha manufacturer's cost")["_validated"]


def test_duplicate_sum_operand_cannot_replace_omitted_requested_entity():
    operations = [{"id":"C1","label":"Total","operation":"sum","inputs":["T1","T1"]}]
    with pytest.raises(ValueError,match="Duplicate calculation operands"):
        validate_research_plan({"tasks":[task(query="Alpha cost",unit="GBP")],"calculations":operations},6,
                               question="Calculate sum of Alpha and Beta costs")
    assert validate_research_plan({"tasks":[task(query="Alpha cost",unit="GBP")],"calculations":operations},6,
                                  question="Calculate double Alpha cost")["_validated"]


def test_named_pair_requires_both_tasks_and_both_calculator_inputs():
    tasks = [task(query="Alpha cost",unit="GBP"),{**task("T2","Beta cost","GBP"),"entity":"Beta"}]
    valid = {"tasks":tasks,"calculations":[{"id":"C1","label":"Total","operation":"sum","inputs":["T1","T2"]}]}
    question = "Calculate sum of Alpha and Beta costs"
    assert validate_research_plan(valid,6,question=question)["_validated"]
    with pytest.raises(ValueError,match="omits the requested entity Beta"):
        validate_research_plan({"tasks":tasks[:1],"calculations":[]},6,question=question)
    only_alpha = {**valid,"calculations":[{"id":"C1","label":"Double","operation":"sum","inputs":["T1","T1"]}]}
    with pytest.raises(ValueError,match="omits inputs for requested entity Beta"):
        validate_research_plan(only_alpha,6,question="Calculate double cost for Alpha and Beta")


def test_planner_schema_prohibits_conditions_when_user_supplied_no_literal_scenario():
    class Generator:
        def count_tokens(self,*args): return 100
        def generate_json(self,system,prompt,**kwargs):
            condition_schema = kwargs["schema"]["properties"]["question_conditions"]
            assert condition_schema["maxItems"]==0
            assert condition_schema["items"]["properties"]["origin"]["enum"]==["user_question"]
            return {"tasks":[task(query="Alpha cost",unit="GBP")],"calculations":[],"clarification":"","question_conditions":[]},{}
    assert build_research_plan("Alpha cost?","en",Generator(),RagConfig())[0]["question_conditions"]==[]


def test_planner_schema_limits_conditions_to_actual_question_literals():
    question = "Assume 50% of Alpha reduction converts into sales"
    class Generator:
        def count_tokens(self,*args): return 100
        def generate_json(self,system,prompt,**kwargs):
            condition_schema = kwargs["schema"]["properties"]["question_conditions"]
            assert condition_schema["maxItems"]==1
            props = condition_schema["items"]["properties"]
            assert props["origin"]["enum"]==["user_question"] and props["raw_text"]["enum"]==[question]
            return {"tasks":[task()],"calculations":[{"id":"C1","label":"Sales","operation":"multiply","inputs":["T1","U1"]}],
                    "clarification":"","question_conditions":[]},{}
    plan,_ = build_research_plan(question,"en",Generator(),RagConfig())
    assert plan["question_conditions"][0]["value"]=="0.5"


def test_arithmetic_generation_schema_blocks_missing_operations_and_phantom_conditions():
    jsonschema = pytest.importorskip("jsonschema")
    valid_task = task(query="Alpha cost",unit="GBP")
    no_operations = {"tasks":[valid_task],"calculations":[],"clarification":"","question_conditions":[]}
    with_choice = {**no_operations,"clarification":"Which cost scenario should be used?"}
    phantom = {"tasks":[valid_task],"calculations":[{"id":"C1","label":"Cost","operation":"multiply","inputs":["T1","U9"]}],
               "clarification":"","question_conditions":[]}
    valid = {**phantom,"tasks":[valid_task,task("T2","Alpha second cost","GBP")],
             "calculations":[{**phantom["calculations"][0],"inputs":["T1","T2"]}]}
    class Generator:
        def count_tokens(self,*args): return 100
        def generate_json(self,system,prompt,**kwargs):
            schema = kwargs["schema"]
            with pytest.raises(jsonschema.ValidationError): jsonschema.validate(no_operations,schema)
            with pytest.raises(jsonschema.ValidationError): jsonschema.validate(phantom,schema)
            jsonschema.validate(with_choice,schema)
            jsonschema.validate(valid,schema)
            return valid,{}
    assert build_research_plan("Calculate sum of Alpha costs","en",Generator(),RagConfig())[0]["_validated"]
def test_missing_sales_scenario_is_clarified_before_model_and_retrieval():
    class Forbidden:
        def count_tokens(self,*args):
            pytest.fail("Missing user choice should precede model I/O")
        def generate_json(self,*args,**kwargs):
            pytest.fail("Missing user choice should precede model I/O")
    from denso_a3.bt6.task_planner import build_research_plan
    plan,_=build_research_plan("Calculate gross margin from illicit-trade reduction targets of Alpha and Beta minus costs.","en",Forbidden(),RagConfig())
    assert plan["clarification"] and plan["tasks"]==[] and plan["calculations"]==[] and plan["question_conditions"]==[]


def test_named_numeric_lookup_in_vietnamese_is_quantity_in_generation_and_reuse():
    import jsonschema
    question = "Điện áp định mức của M1 là bao nhiêu?"
    numeric = {**task(query="M1 nominal voltage",unit="V"),"entity":"M1"}
    text = {**numeric,"kind":"text","expected_unit":"text"}
    invalid = {"tasks":[text],"calculations":[],"clarification":"","question_conditions":[]}
    valid = {**invalid,"tasks":[numeric]}
    with pytest.raises(ValueError,match="numeric field request"):
        validate_research_plan(invalid,6,question=question)
    class Generator:
        def count_tokens(self,*args): return 100
        def generate_json(self,system,prompt,**kwargs):
            with pytest.raises(jsonschema.ValidationError): jsonschema.validate(invalid,kwargs["schema"])
            jsonschema.validate(valid,kwargs["schema"])
            return valid,{}
    assert build_research_plan(question,"vi",Generator(),RagConfig())[0]["tasks"][0]["kind"]=="quantity"
    # Definitions and a sourced manufacturer bridge remain text tasks.
    from denso_a3.bt6.task_planner import direct_quantity_request
    assert not direct_quantity_request("Define the meaning of M1 voltage")
    assert not direct_quantity_request("Find Orion's manufacturer, then its budget")


def test_reused_arithmetic_plan_cannot_drop_the_calculator():
    plan={"tasks":[task(query="Alpha first cost",unit="GBP"),task("T2","Alpha second cost","GBP")],
          "calculations":[],"clarification":"","_validated":True}
    with pytest.raises(ValueError,match="arithmetic requires calculator"):
        validate_research_plan(plan,6,question="Calculate sum of Alpha costs")


@pytest.mark.parametrize("literal,expected,value,dimensions",[
    ("2 MPa","MPa","2000000",{"Pa":1}), ("3 kPa","kPa","3000",{"Pa":1}),
    ("4 m","m","4000",{"mm":1}), ("5 cm","cm","50",{"mm":1}),
    ("50%","%","50",{"percent":1}), ("EUR8 per mille","EUR/stick","0.008",{"EUR":1,"stick":-1}),
])
def test_planner_declared_unit_aliases_match_normalized_source_dimensions(literal,expected,value,dimensions):
    from denso_a3.bt7.fact_extraction import validate_quantity
    actual,unit,actual_dimensions=validate_quantity(literal,expected)
    assert actual==value and actual_dimensions==dimensions
    with pytest.raises(ValueError,match="requested unit"):
        validate_quantity(literal,"V")
