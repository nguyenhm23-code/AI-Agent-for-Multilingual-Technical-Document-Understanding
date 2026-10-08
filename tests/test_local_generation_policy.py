"""Intercept the real local adapter's network boundary, without a model process."""
from urllib.parse import urlsplit

import pytest

from denso_a3.backends.generation import Generator
from denso_a3.bt7.answering import grounded_answer
from denso_a3.contracts import RagConfig, RetrievedEvidence, QueryRequirement
from denso_a3.pipeline.query_budget import QueryBudget


class Response:
    status_code = 200

    def __init__(self, payload=None, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")


def completed(content):
    return Response({"choices": [{"message": {"content": content}}],
                     "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})


def intercepted_local(monkeypatch, responses):
    generator = Generator(RagConfig(), {})
    calls, pending = [], iter(responses)
    # A running process short circuits runtime startup while leaving the real
    # policy guard, count_tokens, payload assembly and network calls intact.
    class Running:
        def poll(self):
            return None
    generator.process = Running()

    def post(url, **kwargs):
        endpoint = urlsplit(url)
        assert endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1" and endpoint.port == generator.config.local_port
        assert generator.session.trust_env is False
        assert kwargs.get("allow_redirects") is False
        calls.append((url, kwargs))
        return Response({"tokens": [1, 2]}) if endpoint.path == "/tokenize" else next(pending)
    monkeypatch.setattr(generator.session, "post", post)
    generator.query_budget = QueryBudget(generator.config)
    return generator, calls


def test_real_adapter_blocks_api_before_all_network(monkeypatch):
    generator = Generator(RagConfig(generation_provider="api", api_enabled=True,
        api_model="cloud", api_base_url="https://outside.example", local_only=True), {})
    def forbidden(*args, **kwargs):
        pytest.fail("API request escaped the local-only guard")
    monkeypatch.setattr(generator.session, "post", forbidden)
    monkeypatch.setattr(generator.session, "get", forbidden)
    with pytest.raises(ValueError, match="local_only"):
        generator.generate_json("system", "prompt")
    with pytest.raises(ValueError, match="local_only"):
        generator.count_tokens("system", "prompt")


def test_real_adapter_repair_and_tokenizer_stay_on_loopback(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "sha.png").write_bytes(b"fixture")
    hit = RetrievedEvidence({"chunk_id": "c1", "source_relative": "manual.pdf", "page_id": "p1", "page_number": 1,
        "image_sha256": "sha", "kind": "text", "text": "M1 pressure 5 MPa"}, 1, evidence_id="E1")
    valid = ('{"status":"answered","claims":[{"text":"Pressure is 5 MPa","support_refs":[{"source_id":"E1",'
             '"quote":"M1 pressure 5 MPa"}]}],"coverage":[{"requirement_id":"R1","state":"supported",'
             '"support_refs":[{"source_id":"E1","quote":"M1 pressure 5 MPa"}]}]}')
    generator, calls = intercepted_local(monkeypatch, [completed("malformed"), completed(valid)])
    result, _, _ = grounded_answer("Pressure?", "en", [hit], tmp_path, generator, max_context_tokens=500,
        requirements=[QueryRequirement("R1", "pressure")], budget=generator.query_budget)
    assert result["status"] == "answered"
    assert [urlsplit(url).path for url, _ in calls] == ["/tokenize", "/v1/chat/completions"] * 2
    assert generator.query_budget.generation_attempts == 2 and generator.query_budget.usage["total_tokens"] == 20
    assert all(len(kwargs["json"]["messages"][1]["content"]) == 1 for url, kwargs in calls if "/chat/" in url)


@pytest.mark.parametrize("method", ["generation", "tokenizer"])
def test_real_adapter_rejects_redirects(monkeypatch, method):
    generator, calls = intercepted_local(monkeypatch, [Response(status=307)])
    if method == "tokenizer":
        def redirected(url, **kwargs):
            assert kwargs.get("allow_redirects") is False
            calls.append((url, kwargs))
            return Response(status=307)
        monkeypatch.setattr(generator.session, "post", redirected)
    with pytest.raises(RuntimeError, match="redirect|HTTP 307"):
        generator.generate_json("system", "prompt") if method == "generation" else generator.count_tokens("system", "prompt")
    assert len(calls) == 1


def test_api_with_local_policy_disabled_still_rejected_before_memory_rewrite(tmp_path, monkeypatch):
    from denso_a3.pipeline import rag_runner
    def forbidden(*args, **kwargs):
        pytest.fail("A question path reached memory or generation before rejecting API")
    monkeypatch.setattr(rag_runner, "ConversationStore", forbidden)
    monkeypatch.setattr(rag_runner, "Generator", forbidden)
    config = RagConfig(orchestration_mode="baseline", local_only=False, generation_provider="api", api_enabled=True,
                       api_model="cloud", api_base_url="https://outside.example")
    with pytest.raises(ValueError, match="local"):
        rag_runner.ask(tmp_path, "Pressure?", config, conversation_id="existing", memory_path=tmp_path / "memory.sqlite")


@pytest.mark.parametrize("health_status", [200, 302])
def test_runtime_health_stays_loopback_and_rejects_redirects(tmp_path, monkeypatch, health_status):
    from denso_a3.backends import generation
    paths = []
    for name in ("server.exe", "model.gguf", "mmproj.gguf"):
        path = tmp_path / name
        path.write_bytes(b"placeholder; process is mocked")
        paths.append(str(path))
    manifest = {"llama_server": paths[0], "models": {"generation": {"model": paths[1], "mmproj": paths[2]}}}
    generator = Generator(RagConfig(), manifest)
    spawned, requests = [], []
    class Process:
        def poll(self):
            return None
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def connect_ex(self, address):
            assert address == ("127.0.0.1", generator.config.local_port)
            return 1
    def spawn(args, **kwargs):
        assert args[args.index("--host") + 1] == "127.0.0.1"
        spawned.append(args)
        return Process()
    def get(url, **kwargs):
        endpoint = urlsplit(url)
        assert endpoint.hostname == "127.0.0.1" and endpoint.path == "/health"
        assert generator.session.trust_env is False and kwargs.get("allow_redirects") is False
        requests.append(url)
        return Response(status=health_status)
    monkeypatch.setattr(generation.socket, "socket", Socket)
    monkeypatch.setattr(generation.subprocess, "Popen", spawn)
    monkeypatch.setattr(generator.session, "get", get)
    if health_status == 302:
        with pytest.raises(RuntimeError, match="redirect"):
            generator.start()
    else:
        generator.start()
    assert len(spawned) == len(requests) == 1
