"""Install the checksum-pinned official Linux x64 CUDA runtime during image build."""
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

RELEASE = "b11235"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/llama-{RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz"
SHA256 = "e5d3bbb3db80df25d0a3d8fa072115b6cb1eae23ad12b8707d1a058ca05b504e"


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        archive = root / "llama.tar.gz"
        urllib.request.urlretrieve(URL, archive)
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != SHA256:
            raise ValueError("Official llama.cpp archive checksum mismatch")
        with tarfile.open(archive) as package:
            package.extractall(root / "unpacked", filter="data")
        source = root / "unpacked" / f"llama-{RELEASE}"
        if not (source / "llama-server").is_file():
            raise FileNotFoundError("llama-server is missing from official archive")
        shutil.copytree(source, "/opt/llama", symlinks=True)


if __name__ == "__main__":
    main()
