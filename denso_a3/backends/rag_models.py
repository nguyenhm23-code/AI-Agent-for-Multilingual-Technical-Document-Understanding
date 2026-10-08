"""Local multilingual encoders. Downloading is an explicit bootstrap step."""

import gc
from pathlib import Path

import numpy as np


class TextEncoder:
    def __init__(self, path, model_name, device="cpu", max_length=512):
        import torch
        from transformers import AutoModel, AutoTokenizer
        torch.set_num_threads(4)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested for embedding but unavailable")
        self.torch, self.name, self.device = torch, model_name, device
        self.max_length = min(max_length, 512 if "e5" in model_name else 8192)
        self.tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        self.model = AutoModel.from_pretrained(str(path), local_files_only=True,
                                              dtype=torch.float16 if device == "cuda" else torch.float32).to(device).eval()
        self.dimension = self.model.config.hidden_size

    def count_tokens(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def encode(self, texts, query=False, batch_size=4):
        torch = self.torch
        # Oversized atomic table rows use windows, never silent tokenizer truncation.
        windows, owners = [], []
        for i, text in enumerate(texts):
            prefix = ("query: " if query else "passage: ") if "e5" in self.name else ""
            ids = self.tokenizer.encode(text, add_special_tokens=False)
            size = self.max_length - 16
            for offset in range(0, max(1, len(ids)), max(1, size-32)):
                windows.append(prefix + self.tokenizer.decode(ids[offset:offset+size], skip_special_tokens=True))
                owners.append(i)
                if offset + size >= len(ids):
                    break
        result = np.zeros((len(texts), self.dimension), dtype=np.float32)
        counts = np.zeros(len(texts), dtype=np.int32)
        for start in range(0, len(windows), batch_size):
            batch = self.tokenizer(windows[start:start+batch_size], padding=True, truncation=True,
                                   max_length=self.max_length, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                hidden = self.model(**batch).last_hidden_state
                if "e5" in self.name:
                    mask = batch["attention_mask"].unsqueeze(-1)
                    vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                else:
                    vectors = hidden[:, 0]
                vectors = torch.nn.functional.normalize(vectors.float(), p=2, dim=1).cpu().numpy()
            for owner, vector in zip(owners[start:start+batch_size], vectors):
                result[owner] += vector
                counts[owner] += 1
        result /= np.maximum(counts[:, None], 1)
        result /= np.maximum(np.linalg.norm(result, axis=1, keepdims=True), 1e-12)
        return result

    def close(self):
        del self.model
        gc.collect()
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()


class Reranker:
    def __init__(self, path, device="cpu", max_length=1024):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(4)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested for reranker but unavailable")
        self.torch, self.device, self.max_length = torch, device, max_length
        self.tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(path), local_files_only=True,
            dtype=torch.float16 if device == "cuda" else torch.float32).to(device).eval()

    def score(self, question, texts):
        scores = []
        for text in texts:
            batch = self.tokenizer([[question, text]], padding=True, truncation=True,
                                   max_length=self.max_length, return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                scores.append(float(self.model(**batch).logits.float().reshape(-1)[0].cpu()))
        return scores

    def close(self):
        del self.model
        gc.collect()
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
