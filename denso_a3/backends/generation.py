"""Managed local llama.cpp and explicit configurable chat API adapter."""

import base64
import io
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import time

import requests
from PIL import Image
from ..contracts import QueryBudgetExceeded


def image_data(path, bbox=None):
    with Image.open(path) as source:
        image = source.convert("RGB")
        if bbox:
            w, h = image.size
            region = (math.ceil(bbox[0]*w), math.ceil(bbox[1]*h), math.floor(bbox[2]*w), math.floor(bbox[3]*h))
            if region[2] <= region[0] or region[3] <= region[1]:
                raise ValueError("Source crop contains no fully allowed pixels")
            image = image.crop(region)
        image.thumbnail((1600, 1600))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


class Generator:
    def __init__(self, config, model_manifest):
        self.config, self.manifest, self.process = config, model_manifest, None
        self.session = requests.Session()
        self.session.trust_env = False if config.generation_provider == "local" else True
        self.startup_ms = 0.0

    def start(self):
        c = self.config
        if c.local_only and c.generation_provider != "local":
            raise ValueError("local_only blocks non-local generation before any endpoint request")
        budget = getattr(self, "query_budget", None)
        if budget:
            budget.check()
        deadline = getattr(self, "deadline", None)
        if deadline is not None and time.perf_counter() >= deadline:
            raise QueryBudgetExceeded("stage_deadline")
        # A request can alternate generation/retrieval phases after close().
        if self.session is None:
            self.session = requests.Session()
            self.session.trust_env = c.generation_provider != "local"
        if c.generation_provider == "api":
            c.validate()
            if not os.environ.get(c.api_key_env):
                raise ValueError(f"Set API credential environment variable {c.api_key_env}")
            return
        if self.process and self.process.poll() is None:
            return
        manifest = self.manifest
        for path in [manifest.get("llama_server", ""), manifest["models"]["generation"]["model"], manifest["models"]["generation"]["mmproj"]]:
            if not Path(path).is_file():
                raise FileNotFoundError("Local model/runtime missing; run rag-bootstrap")
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", c.local_port)) == 0:
                raise RuntimeError(f"Port {c.local_port} already in use; choose another local_port")
        model = manifest["models"]["generation"]
        args = [manifest["llama_server"], "-m", model["model"], "--mmproj", model["mmproj"],
                "--host", "127.0.0.1", "--port", str(c.local_port), "--alias", "denso-local",
                "-ngl", "99", "-c", str(c.context_tokens), "-np", "1", "--image-max-tokens", "1024",
                "--log-disable"]
        started = time.perf_counter()
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = getattr(self,"deadline",None)
        while time.perf_counter()-started < c.timeout_seconds and (deadline is None or time.perf_counter()<deadline):
            if self.process.poll() is not None:
                raise RuntimeError(f"llama-server exited ({self.process.returncode}); check runtime/model compatibility")
            try:
                health = self.session.get(f"http://127.0.0.1:{c.local_port}/health", timeout=2, allow_redirects=False)
                if 300 <= health.status_code < 400:
                    raise RuntimeError("Local health endpoint redirect rejected")
                if health.status_code == 200:
                    self.startup_ms = (time.perf_counter()-started)*1000
                    return
            except requests.RequestException:
                pass
            time.sleep(.3)
        self.close()
        raise TimeoutError("Local llama-server startup timed out")

    def generate_json(self, system, prompt, images=None, max_tokens=None, schema=None):
        budget = getattr(self, "query_budget", None)
        if budget:
            budget.consume_generation()
        self.start()
        c = self.config
        content = [{"type": "text", "text": prompt}]
        content.extend({"type": "image_url", "image_url": {"url": x}} for x in (images or []))
        payload = {"model": "denso-local" if c.generation_provider == "local" else c.api_model,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                   "temperature": 0, "max_tokens": max_tokens or c.max_answer_tokens,
                   "response_format": {"type": "json_object"}}
        if schema and c.generation_provider == "local":
            payload["response_format"]["schema"] = schema
        base = f"http://127.0.0.1:{c.local_port}/v1" if c.generation_provider == "local" else c.api_base_url.rstrip("/")
        headers = {}
        if c.generation_provider == "api":
            headers["Authorization"] = "Bearer " + os.environ[c.api_key_env]
        deadline = getattr(self, "deadline", None)
        timeout = c.timeout_seconds if deadline is None else min(c.timeout_seconds, max(.1, deadline-time.perf_counter()))
        if deadline is not None and time.perf_counter() >= deadline:
            raise QueryBudgetExceeded("stage_deadline")
        response = self.session.post(base + "/chat/completions", json=payload, headers=headers, timeout=timeout, allow_redirects=False)
        if response.status_code >= 300:
            # Do not echo provider response bodies which can contain request text or secrets.
            raise RuntimeError(f"Generation endpoint HTTP {response.status_code}")
        result = response.json()
        text = result["choices"][0]["message"]["content"]
        usage = dict(result.get("usage", {}))
        usage["provider"] = c.generation_provider
        usage["cost_usd"] = 0.0 if c.generation_provider == "local" else None
        if c.generation_provider == "api" and c.api_input_usd_per_million is not None and c.api_output_usd_per_million is not None:
            usage["cost_usd"] = (usage.get("prompt_tokens", 0)*c.api_input_usd_per_million +
                                 usage.get("completion_tokens", 0)*c.api_output_usd_per_million)/1_000_000
        if budget:
            budget.add_usage(usage)
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            if not isinstance(text, str) or "{" not in text or "}" not in text:
                raise ValueError("Model did not return a JSON object")
            data = json.loads(text[text.index("{"):text.rindex("}")+1])
        if not isinstance(data, dict):
            raise ValueError("Model output must be a JSON object")
        return data, usage

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.session is not None:
            self.session.close()
            self.session = None

    def count_tokens(self, system, prompt):
        """Use llama.cpp's actual tokenizer; no embedding-tokenizer approximation."""
        self.start()
        budget = getattr(self, "query_budget", None)
        if budget:
            budget.check()
        if self.config.generation_provider != "local":
            raise ValueError("Hierarchical research requires local tokenizer support")
        deadline = getattr(self, "deadline", None)
        timeout = min(10,self.config.timeout_seconds)
        if deadline is not None:
            timeout = min(timeout, max(.1, deadline-time.perf_counter()))
        response = self.session.post(f"http://127.0.0.1:{self.config.local_port}/tokenize",
            json={"content": system + "\n" + prompt}, timeout=timeout, allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise RuntimeError("Local tokenizer endpoint redirect rejected")
        response.raise_for_status()
        return len(response.json()["tokens"])
