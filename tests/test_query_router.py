import pytest
from denso_a3.bt6.planner import plan_query
from denso_a3.bt6.question_analysis import analyze_query
from denso_a3.bt6.query_router import route_question
from denso_a3.contracts import RagConfig
from denso_a3.pipeline.query_budget import QueryBudget


@pytest.mark.parametrize("question,route,visual", [
    ("What is the voltage of M1?", "baseline", False),
    ("Điện áp của M1 là bao nhiêu?", "baseline", False),
    ("Please read every available source carefully and tell me the rated voltage of M1 with its source page and unit for verification.", "baseline", False),
    ("Find Orion's manufacturer, then its budget.", "hierarchical", False),
    ("Chi phí của nhà sản xuất Orion?", "hierarchical", False),
    ("Compare Alpha and Beta budgets.", "hierarchical", False),
    ("Calculate sum of Alpha and Beta costs.", "hierarchical", False),
    ("Read voltage from the diagram.", "baseline", True),
    ("Read Alpha cost from chart and calculate total.", "hierarchical", True),
])
def test_rules_need_no_classifier(question, route, visual):
    class Forbidden:
        def generate_json(self, *args, **kwargs):
            pytest.fail("Simple rules loaded classifier")
    config = RagConfig()
    budget = QueryBudget(config)
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    decision = route_question(analysis, {"hierarchy": True}, config, Forbidden(), budget)
    assert decision.route==route and decision.needs_visual==visual
    assert budget.router_calls==0


def test_independent_attributes_are_separate_requirements():
    question = "List Orion voltage and weight."
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    assert [r.attribute for r in analysis.requirements]==["voltage", "weight"]
    assert all(r.wording in question for r in analysis.requirements)


def test_ambiguous_local_plan_reused_and_bounded_repair():
    config, question = RagConfig(), "Analyze Alpha and Beta budgets."
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    plan = {"tasks": [{"task_id": f"T{i}", "query": entity+" budget", "label": "budget", "entity": entity,
                        "kind": "quantity", "depends_on": [], "expected_unit": "GBP"} for i,entity in enumerate(("Alpha", "Beta"),1)],
            "calculations": [], "clarification": "", "question_conditions": []}
    class Local:
        calls=0
        def count_tokens(self, *args): return 100
        def generate_json(self, *args, **kwargs):
            self.calls+=1
            return ({} if self.calls==1 else plan), {}
    gen, budget = Local(), QueryBudget(config)
    budget.active_stage="router"
    decision=route_question(analysis, {"hierarchy": True}, config, gen, budget)
    assert decision.route=="hierarchical" and decision.planner_reused
    assert decision.research_plan["_validated"] and gen.calls==budget.router_calls==2


def test_legacy_alias_preserves_visual_eligibility():
    for strategy,policy in (("auto","auto"),("visual","required")):
        plan=plan_query("Voltage?", {"documents": {}}, strategy)
        assert plan.strategy=="hybrid" and plan.visual_policy==policy
