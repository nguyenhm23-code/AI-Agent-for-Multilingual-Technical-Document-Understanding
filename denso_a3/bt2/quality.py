def quality_signals(text: str, spans: list[dict]) -> dict:
    scores = [s["score"] for s in spans if s.get("score") is not None]
    return {"review_status": "unreviewed", "empty_text": not text.strip(),
            "replacement_characters": text.count("\ufffd"), "characters": len(text),
            "span_count": len(spans),
            "low_score_span_fraction_below_0_5": sum(s < 0.5 for s in scores) / len(scores) if scores else None,
            "confidence_is_accuracy": False}
