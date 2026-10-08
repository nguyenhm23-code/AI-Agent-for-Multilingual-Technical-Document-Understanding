"""One persistent spawned GPU worker; terminate it on an RPC deadline."""

import multiprocessing as mp
import traceback

from ..contracts import PipelineConfig


def _serve(connection, config, allow_download):
    try:
        from ..backends.docling_backend import DoclingBackend
        backend = DoclingBackend(config, allow_download=allow_download)
        connection.send({"ok": True, "runtime": backend.runtime})
        while True:
            request = connection.recv()
            if request is None:
                break
            try:
                connection.send({"ok": True, "payload": backend.convert(request)})
            except Exception as exc:
                connection.send({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                 "traceback": traceback.format_exc()})
    except Exception as exc:
        try:
            connection.send({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                             "traceback": traceback.format_exc()})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class InferenceWorker:
    def __init__(self, config: PipelineConfig, allow_download: bool = False):
        self.config, self.allow_download = config, allow_download
        self.process = self.connection = None
        self.runtime = None

    def start(self):
        context = mp.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_serve, args=(child, self.config, self.allow_download), daemon=True)
        self.process.start()
        child.close()
        response = self._receive(self.config.startup_timeout_seconds, "model_startup")
        if not response["ok"]:
            self.close()
            raise RuntimeError(response["error"] + "\n" + response.get("traceback", ""))
        self.runtime = response["runtime"]
        return self.runtime

    def _receive(self, seconds, stage):
        if not self.connection.poll(seconds):
            self.close()
            raise TimeoutError(f"{stage} exceeded {seconds}s; worker terminated")
        try:
            return self.connection.recv()
        except (EOFError, OSError) as exc:
            self.close()
            raise RuntimeError(f"{stage}: worker exited without response") from exc

    def convert(self, image_path):
        if self.process is None:
            previous = self.runtime
            self.start()
            if previous and (previous["model_fingerprint"] != self.runtime["model_fingerprint"] or previous["versions"] != self.runtime["versions"]):
                self.close()
                raise RuntimeError("Models/versions changed after worker restart; start a new run")
        self.connection.send(image_path)
        response = self._receive(self.config.timeout_seconds, "joint_bt2_bt3")
        if not response["ok"]:
            raise RuntimeError(response["error"])
        return response["payload"]

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                try:
                    self.connection.send(None)
                except (OSError, BrokenPipeError):
                    pass
                self.process.join(timeout=2)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(timeout=5)
            self.process.close()
            self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
