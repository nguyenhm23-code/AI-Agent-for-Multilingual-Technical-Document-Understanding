import re
import unicodedata
from rapidfuzz.distance import Levenshtein


def normalize(text: str, mode: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\s+", " ", text).strip() if mode == "whitespace" else text


def compare(reference: str, prediction: str, mode: str) -> dict:
    reference, prediction = normalize(reference, mode), normalize(prediction, mode)
    result = {}
    for name, truth, hypothesis in [("cer", reference, prediction),
                                     ("wer", reference.split(), prediction.split())]:
        operations = Levenshtein.editops(truth, hypothesis)
        counts = {"substitutions": 0, "deletions": 0, "insertions": 0}
        for op in operations:
            counts[{"replace": "substitutions", "delete": "deletions", "insert": "insertions"}[op.tag]] += 1
        result[name] = {**counts, "edits": len(operations), "reference_length": len(truth),
                        "rate": len(operations) / len(truth) if len(truth) else None,
                        "empty_reference": not truth}
    return result


def aggregate(comparisons: list[dict]) -> dict:
    result = {}
    for metric in ("cer", "wer"):
        edits = sum(x[metric]["edits"] for x in comparisons)
        length = sum(x[metric]["reference_length"] for x in comparisons)
        rates = [x[metric]["rate"] for x in comparisons if x[metric]["rate"] is not None]
        result[metric] = {"micro": edits / length if length else None,
                          "macro": sum(rates) / len(rates) if rates else None,
                          "edits": edits, "reference_length": length}
    return result
