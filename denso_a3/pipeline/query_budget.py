"""One monotonic deadline and counters for every attempt of a user request."""
import time
from ..contracts import QueryBudgetExceeded


class QueryBudget:
    def __init__(self, config, clock=time.perf_counter):
        self.config, self.clock = config, clock
        self.started = clock()
        self.deadline = self.started + config.query_deadline_seconds
        self.retrieval_calls = self.planner_calls = self.router_calls = 0
        self.generation_attempts = self.visual_calls = self.escalations = 0
        self.verification_calls = 0
        self.stop_reason = ""
        self.visual_cache = {}
        self.usage = {}

    def check(self):
        if self.clock() >= self.deadline:
            self.stop_reason = "query_deadline"
            raise QueryBudgetExceeded(self.stop_reason)

    def _consume(self, name, maximum):
        self.check()
        if getattr(self, name) >= maximum:
            self.stop_reason = name + "_budget"
            raise QueryBudgetExceeded(self.stop_reason)
        setattr(self, name, getattr(self, name) + 1)

    def consume_retrieval(self):
        self._consume("retrieval_calls", self.config.query_max_retrieval_calls)

    def consume_planner(self):
        self.check()
        self.planner_calls += 1

    def consume_router(self):
        self._consume("router_calls", self.config.router_max_llm_calls)

    def consume_visual(self):
        self._consume("visual_calls", self.config.query_max_visual_calls)

    def consume_escalation(self):
        self._consume("escalations", self.config.query_max_escalations)

    def consume_generation(self):
        self.check()
        self.generation_attempts += 1

    def consume_verification(self):
        self._consume("verification_calls", self.config.verification_max_calls)

    def add_usage(self, usage):
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                self.usage[key] = self.usage.get(key, 0) + value
            elif key not in self.usage:
                self.usage[key] = value

    def to_dict(self):
        now = self.clock()
        return {name: getattr(self, name) for name in
                ("retrieval_calls", "planner_calls", "router_calls", "generation_attempts", "visual_calls", "verification_calls", "escalations", "stop_reason")} | {
                    "elapsed_seconds": now-self.started, "remaining_seconds": max(0, self.deadline-now),
                    "deadline_seconds": self.config.query_deadline_seconds}
