"""Container integration contracts without downloading models or requiring a GPU."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from denso_a3.pipeline import container_cli, rag_cli
from denso_a3.backends import bootstrap


def test_pipeline_stops_before_index_on_extraction_failure(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(container_cli, "execute", lambda args: calls.append(args) or 7)
    assert container_cli.main(["pipeline", "--output", str(tmp_path / "run"), "--question", "q"]) == 7
    assert [c[0] for c in calls] == ["run"]


def test_pipeline_passes_artifacts_and_question_between_stages(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(container_cli, "execute", lambda args: calls.append(args) or 0)
    output = tmp_path / "new"
    assert container_cli.main(["pipeline", "--output", str(output), "--input", "docs", "--language", "vi",
                               "--limit", "1", "--source", "images/page.png",
                               "--question", "What is shown?", "--config", "config.json"]) == 0
    assert [c[0] for c in calls] == ["run", "rag-build", "rag-ask"]
    assert calls[0][calls[0].index("--output") + 1] == calls[1][calls[1].index("--run") + 1]
    assert calls[1][calls[1].index("--output") + 1] == calls[2][calls[2].index("--index") + 1]
    assert calls[2][calls[2].index("--output") + 1] == str(output / "answer.json")
    assert calls[2][calls[2].index("--source") + 1] == "images/page.png"
    assert calls[2][calls[2].index("--question") + 1] == "What is shown?"


def test_pipeline_refuses_existing_output(tmp_path, monkeypatch):
    monkeypatch.setattr(container_cli, "execute", lambda _: pytest.fail("Must not launch a stage"))
    with pytest.raises(SystemExit):
        container_cli.main(["pipeline", "--output", str(tmp_path)])


def test_bootstrap_stops_if_gpu_doctor_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(container_cli, "execute", lambda args: calls.append(args) or 1)
    assert container_cli.main(["bootstrap-all", "--language", "vi"]) == 1
    assert calls == [["doctor", "--language", "vi", "--download-models"]]


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_ui_bind_address(monkeypatch, host):
    calls = []
    monkeypatch.setattr(rag_cli.subprocess, "call", lambda args, **kwargs: calls.append(args) or 0)
    args = ["rag-ui"] + (["--host", host] if host != "127.0.0.1" else [])
    assert rag_cli.main(args) == 0
    assert calls[0][calls[0].index("--server.address") + 1] == host


def test_bootstrap_uses_supplied_runtime_without_windows_download(tmp_path, monkeypatch):
    import huggingface_hub
    executable = tmp_path / "llama-server"
    executable.write_bytes(b"fake runtime")
    executable.chmod(0o755)
    monkeypatch.setenv("DENSO_LLAMA_SERVER", str(executable))
    names = ["model.safetensors", "model-q4_k_m.gguf", "mmproj-q8_0.gguf"]
    info = SimpleNamespace(sha="revision", siblings=[SimpleNamespace(rfilename=n) for n in names])
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda _: info))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: None)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda repo, name, **kw: str(kw["local_dir"] / name))
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", lambda *a: pytest.fail("Windows runtime download"))
    result = bootstrap.bootstrap(tmp_path / "cache")
    assert result["llama_server"] == str(executable.resolve())
    assert result["llama_server_sha256"] == bootstrap.sha256_file(executable)
    assert json.loads((tmp_path / "cache/models.json").read_text())["llama_server"] == result["llama_server"]


def test_invalid_runtime_fails_before_model_download(tmp_path, monkeypatch):
    monkeypatch.setenv("DENSO_LLAMA_SERVER", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="DENSO_LLAMA_SERVER"):
        bootstrap.bootstrap(tmp_path / "cache")
    assert not (tmp_path / "cache").exists()
