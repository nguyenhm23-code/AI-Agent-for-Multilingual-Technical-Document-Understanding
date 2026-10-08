"""Shared contracts. JSON geometry uses the canonical page-image frame."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import math

SCHEMA_VERSION = "1.0"
RAG_SCHEMA_VERSION = "1.0"
HIERARCHY_VERSION = "1.0"
SUPPORTED_RAG_SCHEMAS = {"1.0", "1.1"}


class ContextBudgetExceeded(ValueError):
    """Required evidence cannot fit without dropping a planned input."""


class QueryBudgetExceeded(TimeoutError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass
class QueryRequirement:
    requirement_id: str
    label: str
    entity: str = ""
    attribute: str = ""
    wording: str = ""


@dataclass
class QueryAnalysis:
    question: str
    search_question: str
    language: str
    requirements: list = field(default_factory=list)
    needs_calculation: bool = False
    needs_dependency: bool = False
    needs_comparison: bool = False
    needs_visual: bool = False
    needs_clarification: bool = False
    rule_hits: list = field(default_factory=list)
    ambiguous: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class RouteDecision:
    route: str
    source: str = "rules"
    reason_codes: list = field(default_factory=list)
    reason: str = ""
    needs_visual: bool = False
    capability_check: dict = field(default_factory=dict)
    planner_reused: bool = False
    research_plan: dict | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class EvidenceAssessment:
    state: str
    requirement_ids: list = field(default_factory=list)
    support: list = field(default_factory=list)
    needs_visual: bool = False
    next_action: str = ""
    reason_codes: list = field(default_factory=list)
    validation_errors: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class AnswerVerification:
    state: str
    claims: list = field(default_factory=list)
    question_supported: bool = False
    reason_codes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    attempts: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class MetadataSuggestion:
    field: str
    value: str
    origin: str
    evidence: dict
    review_status: str = "unreviewed"
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class QuestionCondition:
    condition_id: str
    kind: str
    raw_text: str
    start: int
    end: int
    origin: str
    applies_to: str
    value: str = ""
    unit: str = ""
    dimensions: dict = field(default_factory=dict)
    conversion_trace: list = field(default_factory=list)
    source_text: str = ""


@dataclass
class VisualEvidence:
    image_id: str
    image_sha256: str
    image_asset: str
    document_id: str
    page_id: str
    page_number: int | None
    source_relative: str
    chunk_ids: list = field(default_factory=list)
    source_bbox: list | None = None


@dataclass
class VisualObservation:
    observation_id: str
    image_id: str
    requirement_id: str = ""
    task_id: str = ""
    state: str = "unreadable"
    observed_text: str = ""
    raw_value: str = ""
    unit_text: str = ""
    entity_label: str = ""
    row_label: str = ""
    column_label: str = ""
    provenance: dict = field(default_factory=dict)


@dataclass
class VisualPhaseResult:
    selected_images: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    missing_requirement_ids: list = field(default_factory=list)
    ambiguous_requirement_ids: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    trace: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class HierarchyNode:
    node_id: str
    level: str
    document_id: str
    parent_node_id: str | None
    heading: str
    chunk_ids: list[str]
    page_ids: list[str]
    routing_text: str
    origin: str = "extractive"


@dataclass
class ResearchTask:
    task_id: str
    query: str
    label: str
    entity: str = ""
    kind: str = "text"
    depends_on: list[str] = field(default_factory=list)
    expected_unit: str = ""


@dataclass
class EvidenceFact:
    task_id: str
    value: str
    unit: str
    quote: str
    chunk_id: str
    raw_value: str
    period: str = ""
    scenario: str = ""
    dimensions: dict[str, int] = field(default_factory=dict)
    origin: str = "source_text"
    support_kind: str = "text"
    visual_observation_id: str | None = None
    image_id: str | None = None
    support_text: str = ""


@dataclass
class ChunkRecord:
    chunk_id: str
    content_key: str
    page_id: str
    document_id: str
    source_relative: str
    source_sha256: str
    image_sha256: str
    page_number: int | None
    kind: str
    text: str
    parent_id: str
    heading: str = ""
    block_ids: list[str] = field(default_factory=list)
    span_ids: list[str] = field(default_factory=list)
    bbox: list[float] | None = None
    table: dict | None = None
    quality_flags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class QueryPlan:
    original_question: str
    search_question: str
    language: str
    strategy: str = "auto"
    hard_filters: dict = field(default_factory=dict)
    soft_preferences: dict = field(default_factory=dict)
    intents: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    clarification: str | None = None
    visual_policy: str = "auto"
    requested_strategy: str | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class RetrievedEvidence:
    chunk: dict
    score: float
    scores: dict = field(default_factory=dict)
    evidence_id: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class AnswerRecord:
    question: str
    answer: str
    status: str
    language: str
    citations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    timings_ms: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class RagConfig:
    model_manifest: str = ".cache/rag/models.json"
    embedding_role: str = "embedding"
    build_device: str = "cuda"
    query_device: str = "cpu"
    rerank_device: str = "cpu"
    chunk_tokens: int = 384
    overlap_tokens: int = 48
    chunk_policy: str = "table-aware"
    candidates: int = 20
    rerank_candidates: int = 12
    evidence_limit: int = 5
    rerank: bool = True
    metadata_boost: bool = True
    generation_provider: str = "local"
    api_base_url: str = ""
    api_model: str = ""
    api_key_env: str = "DENSO_API_KEY"
    api_enabled: bool = False
    api_input_usd_per_million: float | None = None
    api_output_usd_per_million: float | None = None
    local_port: int = 8091
    max_answer_tokens: int = 600
    timeout_seconds: int = 180
    context_tokens: int = 8192
    caption_images: bool = True
    orchestration_mode: str = "auto"
    retrieval_strategy: str = "hybrid"
    visual_policy: str = "auto"
    local_only: bool = True
    query_deadline_seconds: int = 300
    query_max_retrieval_calls: int = 12
    query_max_escalations: int = 1
    router_max_llm_calls: int = 2
    visual_max_images_per_call: int = 2
    query_max_visual_calls: int = 6
    build_hierarchy: bool = True
    research_max_tasks: int = 6
    research_max_rounds: int = 3
    research_max_calls: int = 12
    research_deadline_seconds: int = 300
    research_context_tokens: int = 4500
    hierarchy_documents: int = 6
    hierarchy_pages: int = 4
    research_evidence_per_task: int = 5
    verify_answers: bool = True
    verification_max_calls: int = 2
    auto_metadata_suggestions: bool = True
    metadata_use_llm: bool = True
    metadata_max_calls_per_document: int = 2

    def validate(self):
        if any(not isinstance(v, bool) for v in (self.verify_answers, self.auto_metadata_suggestions, self.metadata_use_llm)):
            raise ValueError("Verification/metadata flags must be boolean")
        if not 1 <= self.verification_max_calls <= 2 or not 1 <= self.metadata_max_calls_per_document <= 2:
            raise ValueError("Verification/metadata calls must be between 1 and 2")
        if self.orchestration_mode not in {"auto", "baseline", "hierarchical"}:
            raise ValueError("orchestration_mode must be auto, baseline or hierarchical")
        if self.local_only and self.generation_provider != "local":
            raise ValueError("local_only requires local generation; API providers are blocked")
        if self.retrieval_strategy not in {"hybrid", "auto", "visual", "lexical", "dense"} or self.visual_policy not in {"auto", "required", "off"}:
            raise ValueError("Invalid retrieval strategy/visual policy")
        if self.query_deadline_seconds < 1 or not 1 <= self.query_max_retrieval_calls <= 60 or not 0 <= self.query_max_escalations <= 1:
            raise ValueError("Invalid query deadline/retrieval/escalation budget")
        if not 1 <= self.router_max_llm_calls <= 2 or not 1 <= self.visual_max_images_per_call <= 2 or not 1 <= self.query_max_visual_calls <= 60:
            raise ValueError("Invalid router/visual budget")
        if self.orchestration_mode=="hierarchical" and self.generation_provider!="local":
            raise ValueError("Hierarchical research currently requires local generation/tokenizer")
        if not 1 <= self.research_max_tasks <= 12 or not 1 <= self.research_max_rounds <= 5:
            raise ValueError("Invalid research task/round budget")
        if not 1 <= self.research_max_calls <= 60 or self.research_deadline_seconds < 1:
            raise ValueError("Invalid research call/deadline budget")
        if not 1 <= self.hierarchy_documents <= 20 or not 1 <= self.hierarchy_pages <= 20:
            raise ValueError("Invalid hierarchy routing budget")
        if not 1 <= self.research_evidence_per_task <= 10:
            raise ValueError("Invalid evidence budget per task")
        if self.orchestration_mode=="hierarchical" and not 256 <= self.research_context_tokens <= self.context_tokens - self.max_answer_tokens - 512:
            raise ValueError("Research context must leave room for prompts and answer")
        if self.generation_provider not in {"local", "api"}:
            raise ValueError("generation_provider must be local or api")
        if self.generation_provider == "api" and not (self.api_enabled and self.api_base_url and self.api_model):
            raise ValueError("API use requires api_enabled, api_base_url and api_model")
        for device in (self.build_device, self.query_device, self.rerank_device):
            if device not in {"cpu", "cuda"}:
                raise ValueError("Model devices must be explicit cpu or cuda")
        if not 1 <= self.evidence_limit <= self.rerank_candidates <= self.candidates <= 100:
            raise ValueError("Require evidence_limit <= rerank_candidates <= candidates <=100")
        if not 64 <= self.chunk_tokens <= 2048 or not 0 <= self.overlap_tokens < self.chunk_tokens:
            raise ValueError("Invalid chunk token budget")
        if not 1024 <= self.context_tokens <= 32768 or not 64 <= self.max_answer_tokens <= 4096:
            raise ValueError("Invalid generation token budget")
        if self.chunk_policy not in {"flat", "table-aware"}:
            raise ValueError("chunk_policy must be flat or table-aware")
        if not 1024 <= self.local_port <= 65535 or self.timeout_seconds < 1:
            raise ValueError("Invalid server port or timeout")

    def to_dict(self):
        return asdict(self)


@dataclass
class PageInput:
    page_id: str
    document_id: str
    source_path: str
    source_relative: str
    source_sha256: str
    page_number: int | None
    page_type: str
    image_path: str | None = None
    image_sha256: str | None = None
    width: int = 0
    height: int = 0
    status: str = "ok"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PipelineConfig:
    input_dir: str
    cache_dir: str
    model_dir: str
    dpi: int = 200
    device: str = "cuda"
    ocr_backend: str = "torch"
    language: str = "en"
    threads: int = 4
    timeout_seconds: int = 180
    startup_timeout_seconds: int = 600
    table_mode: str = "accurate"
    force_ocr: bool = True

    def validate(self) -> None:
        if self.dpi < 72 or self.dpi > 600:
            raise ValueError("DPI must be between 72 and 600")
        if self.device not in {"cuda", "cpu"}:
            raise ValueError("device must be cuda or cpu; no silent fallback")
        if self.ocr_backend != "torch":
            raise ValueError("This validated demo uses RapidOCR Torch; another backend requires a new pilot")
        if self.language not in {"en", "vi"}:
            raise ValueError("Supported OCR languages are en and vi")
        if self.threads < 1 or self.timeout_seconds < 1:
            raise ValueError("threads and timeout must be positive")
        if self.startup_timeout_seconds < 1 or not self.force_ocr:
            raise ValueError("Architecture C demo requires fresh OCR and a positive startup timeout")
        if not Path(self.input_dir).is_dir():
            raise ValueError("input_dir must be an existing directory")

    def inference_dict(self) -> dict:
        # No reference/QA paths are part of inference configuration.
        return {k: v for k, v in asdict(self).items()
                if k not in {"input_dir", "cache_dir", "model_dir", "startup_timeout_seconds"}}


def checked_box(values: list[float], width: float, height: float) -> list[float]:
    """Validate normalized top-left geometry, tolerating only rounding noise."""
    if width <= 0 or height <= 0 or len(values) != 4:
        raise ValueError("Invalid frame/bbox")
    box = [float(x) for x in values]
    if not all(math.isfinite(x) for x in box):
        raise ValueError("Non-finite bbox")
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Zero-area or inverted bbox")
    if x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1:
        raise ValueError("BBox outside canonical page")
    return [max(0.0, min(1.0, x0 / width)), max(0.0, min(1.0, y0 / height)),
            max(0.0, min(1.0, x1 / width)), max(0.0, min(1.0, y1 / height))]
