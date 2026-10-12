"""Explicit downloads from official model and llama.cpp repositories."""

import json
import os
from pathlib import Path
import urllib.request
import zipfile

from ..io import sha256_file, write_json

LLAMA_RELEASE = "b11235"


def bootstrap(root: Path, include_e5=False):
    # Containers supply a Linux CUDA runtime built from the same pinned release.
    supplied_server = os.environ.get("DENSO_LLAMA_SERVER")
    if supplied_server:
        server = Path(supplied_server).resolve()
        if not server.is_file() or not os.access(server, os.X_OK):
            raise FileNotFoundError(f"DENSO_LLAMA_SERVER is not an executable file: {server}")
    elif os.name != "nt":
        raise RuntimeError("Set DENSO_LLAMA_SERVER to a Linux llama-server executable, or use the Docker image")
    from huggingface_hub import HfApi, snapshot_download, hf_hub_download
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"
    api = HfApi()
    manifest = {"models": {}, "llama_release": LLAMA_RELEASE}
    for role, repo in [("embedding", "BAAI/bge-m3"), ("reranker", "BAAI/bge-reranker-v2-m3")] + (
            [("embedding_e5", "intfloat/multilingual-e5-large")] if include_e5 else []):
        info = api.model_info(repo)
        names = [x.rfilename for x in info.siblings]
        weights = "*.safetensors" if any(n.endswith(".safetensors") and "/" not in n for n in names) else "pytorch_model.bin"
        destination = root / "models" / repo.replace("/", "--")
        print(f"Downloading {role}: {repo}@{info.sha}", flush=True)
        snapshot_download(repo, revision=info.sha, local_dir=destination, max_workers=2,
                          allow_patterns=[weights, "*.json", "*.model", "*.txt"],
                          ignore_patterns=["onnx/*", "openvino/*", "imgs/*", "imgs/**/*"])
        manifest["models"][role] = {"repo": repo, "revision": info.sha, "path": str(destination)}
        write_json(root / "models.json", manifest)
    repo = "Qwen/Qwen3-VL-2B-Instruct-GGUF"
    info = api.model_info(repo)
    files = [x.rfilename for x in info.siblings]
    language = next(n for n in files if "q4_k_m" in n.lower() and "mmproj" not in n.lower())
    projector = next(n for n in files if "mmproj" in n.lower() and "q8_0" in n.lower())
    target = root / "models" / "Qwen3-VL-2B"
    print(f"Downloading VLM: {repo}@{info.sha}", flush=True)
    paths = [hf_hub_download(repo, n, revision=info.sha, local_dir=target) for n in (language, projector)]
    manifest["models"]["generation"] = {"repo": repo, "revision": info.sha, "model": paths[0], "mmproj": paths[1]}
    write_json(root / "models.json", manifest)
    if supplied_server:
        manifest["llama_server"] = str(server)
        manifest["llama_server_sha256"] = sha256_file(server)
        write_json(root / "models.json", manifest)
        print("Bootstrap complete: " + str(root / "models.json"), flush=True)
        return manifest
    release = json.load(urllib.request.urlopen(f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{LLAMA_RELEASE}"))
    wanted = {f"llama-{LLAMA_RELEASE}-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"}
    runtime = root / "llama" / LLAMA_RELEASE
    runtime.mkdir(parents=True, exist_ok=True)
    for asset in release["assets"]:
        if asset["name"] not in wanted:
            continue
        archive = root / asset["name"]
        if not archive.is_file() or archive.stat().st_size != asset["size"]:
            print(f"Downloading runtime: {asset['name']}", flush=True)
            urllib.request.urlretrieve(asset["browser_download_url"], archive)
        digest = asset.get("digest")
        if digest and digest.startswith("sha256:") and sha256_file(archive) != digest[7:]:
            raise ValueError("llama.cpp release checksum mismatch")
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                if not (runtime / member.filename).resolve().is_relative_to(runtime.resolve()):
                    raise ValueError("Unsafe runtime archive path")
            z.extractall(runtime)
    executables = list(runtime.rglob("llama-server.exe"))
    if not executables:
        raise FileNotFoundError("llama-server.exe missing in downloaded release")
    manifest["llama_server"] = str(executables[0])
    manifest["llama_server_sha256"] = sha256_file(executables[0])
    write_json(root / "models.json", manifest)
    print("Bootstrap complete: " + str(root / "models.json"), flush=True)
    return manifest
