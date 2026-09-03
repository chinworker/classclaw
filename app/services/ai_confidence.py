from __future__ import annotations

from collections.abc import Iterable
from typing import Any

AI_CONFIDENCE_THRESHOLD = 0.75


def _text_items(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip()[:500] for item in value if str(item).strip()]
    return list(dict.fromkeys(items))[:limit]


def evaluate_ai_output(
    result: dict[str, Any],
    *,
    blocking_reasons: Iterable[str] = (),
    default_summary: str = "分析完成",
) -> dict[str, Any]:
    """Normalize the shared AI confidence contract and decide whether its data may be used."""
    raw_confidence = result.get("confidence")
    confidence = (
        float(raw_confidence)
        if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool) and 0 <= raw_confidence <= 1
        else None
    )
    ai_reasons = _text_items(result.get("reasons"))
    blockers = list(dict.fromkeys(str(item).strip()[:500] for item in blocking_reasons if str(item).strip()))[:20]
    contract_reasons: list[str] = []
    if confidence is None:
        contract_reasons.append("AI 未返回有效的 0 到 1 置信度")
    if not ai_reasons:
        contract_reasons.append("AI 未返回判断置信度所依据的原因项")
    # Backend validation findings take precedence over an AI's potentially
    # contradictory claim that its output was clear.
    reasons = list(dict.fromkeys([*(blockers or ai_reasons), *contract_reasons]))
    accepted = confidence is not None and confidence >= AI_CONFIDENCE_THRESHOLD and bool(ai_reasons) and not blockers
    return {
        "accepted": accepted,
        "confidence": confidence,
        "threshold": AI_CONFIDENCE_THRESHOLD,
        "reasons": reasons,
        "summary": str(result.get("summary") or default_summary)[:500],
    }
