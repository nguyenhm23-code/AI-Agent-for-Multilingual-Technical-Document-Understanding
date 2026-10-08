import pytest
from denso_a3.bt6.task_planner import build_research_plan
from denso_a3.contracts import RagConfig


def test_resolved_name_is_planning_context_but_not_a_condition_authority():
    question="Find its manufacturer, then its budget."
    resolved="Find Orion's manufacturer, then its budget."
    plan={"tasks":[{"task_id":"T1","query":"Orion manufacturer","label":"Manufacturer","entity":"Orion","kind":"text","expected_unit":"text","depends_on":[]},
                   {"task_id":"T2","query":"{T1} budget","label":"Budget","entity":"{T1}","kind":"quantity","expected_unit":"GBP","depends_on":["T1"]}],
          "calculations":[],"question_conditions":[],"clarification":""}
    class Model:
        def count_tokens(self,*args): return 100
        def generate_json(self,system,prompt,**kwargs):
            assert 'resolved_search_question' in prompt and resolved in prompt
            return plan,{}
    result,_=build_research_plan(question,"en",Model(),RagConfig(),search_question=resolved)
    assert result["_question"]==question and result["_search_question"]==resolved and result["_validated"]
    with pytest.raises(ValueError,match="valid research plan"):
        forged={"condition_id":"U1","kind":"ratio","raw_text":"100% sales transfer","origin":"user_question","applies_to":"sales transfer"}
        plan["question_conditions"]=[forged]
        build_research_plan(question,"en",Model(),RagConfig(),search_question=resolved+" Assume 100% sales transfer")
