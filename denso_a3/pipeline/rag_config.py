from pathlib import Path

from ..backends.artifacts import read_json
from ..contracts import RagConfig

PROJECT = Path(__file__).resolve().parents[2]


def load_config(path=None):
    values = read_json(Path(path)) if path else {}
    if "research_assumptions" in values:
        raise ValueError("research_assumptions was removed; state explicit conditions in the question or clarification")
    config = RagConfig(**values)
    config.validate()
    manifest_path = Path(config.model_manifest)
    if not manifest_path.is_absolute():
        config.model_manifest = str(PROJECT / manifest_path)
    return config


def models(config):
    return read_json(Path(config.model_manifest))
