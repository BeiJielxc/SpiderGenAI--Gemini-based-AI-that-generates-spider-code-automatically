"""
Task critic/validator.

Provides rule-based acceptance checks and optional LLM-assisted verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CriticIssue:
    severity: str
    code: str
    message: str


@dataclass
class CriticVerdict:
    passed: bool
    summary: str
    confidence: float
    issues: List[CriticIssue] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "confidence": self.confidence,
            "issues": [issue.__dict__ for issue in self.issues],
            "recommendations": self.recommendations,
            "details": self.details,
        }


class Critic:
    """Rule-first critic. Optional LLM can be attached later for semantic checks."""

    def __init__(self, llm_agent=None):
        self.llm_agent = llm_agent

    def evaluate_generated_code(
        self,
        code: str,
        run_mode: str,
        objective: str = "",
        min_items: int = 1,
    ) -> CriticVerdict:
        issues: List[CriticIssue] = []
        recommendations: List[str] = []

        stripped = (code or "").strip()
        if not stripped:
            issues.append(CriticIssue("error", "empty_code", "Generated code is empty."))
            return CriticVerdict(
                passed=False,
                summary="Generated code is empty.",
                confidence=1.0,
                issues=issues,
                recommendations=["Run page analysis and regenerate code."],
            )

        # Rule 1: runnable structure
        if "if __name__ == \"__main__\"" not in code:
            issues.append(CriticIssue("warning", "missing_main_guard", "Missing main entry guard."))
            recommendations.append("Add an executable entry point with main guard.")

        # Rule 2: output persistence
        has_json_persistence = ("json.dump(" in code) or ("save_results" in code)
        if not has_json_persistence:
            issues.append(CriticIssue("error", "missing_output", "No JSON output persistence detected."))
            recommendations.append("Persist extraction output to JSON for downstream use.")

        # Rule 3: extraction backend exists
        has_extraction_backend = any(token in code for token in ["requests", "playwright", "httpx", "BeautifulSoup"])
        if not has_extraction_backend:
            issues.append(CriticIssue("error", "missing_backend", "No extraction backend detected."))
            recommendations.append("Use requests/httpx or playwright to fetch data.")

        # Rule 4: mode-specific fields
        if run_mode == "enterprise_report":
            for field in ["downloadUrl", "fileType", "name", "date"]:
                if field not in code:
                    issues.append(CriticIssue("warning", "field_missing", f"Expected field '{field}' not found in code."))
        elif run_mode == "news_sentiment":
            for field in ["title", "sourceUrl", "date"]:
                if field not in code:
                    issues.append(CriticIssue("warning", "field_missing", f"Expected field '{field}' not found in code."))

        # Rule 5: task objective hint
        if objective and len(objective.strip()) > 0 and "TODO" in code:
            issues.append(CriticIssue("warning", "todo_left", "Code still contains TODO placeholders."))
            recommendations.append("Resolve TODO placeholders before finishing.")

        # Rule 6: very small script warning
        if len(code) < 400:
            issues.append(CriticIssue("warning", "suspiciously_short", "Generated code is unusually short."))
            recommendations.append("Re-run analysis and regenerate with more context.")

        error_count = sum(1 for it in issues if it.severity == "error")
        warning_count = sum(1 for it in issues if it.severity == "warning")

        passed = error_count == 0
        if passed and warning_count == 0:
            summary = "Critic passed with no issues."
            confidence = 0.92
        elif passed:
            summary = f"Critic passed with {warning_count} warning(s)."
            confidence = 0.78
        else:
            summary = f"Critic failed with {error_count} error(s) and {warning_count} warning(s)."
            confidence = 0.9

        if not recommendations and not passed:
            recommendations.append("Fallback to alternate tool path and regenerate code.")

        return CriticVerdict(
            passed=passed,
            summary=summary,
            confidence=confidence,
            issues=issues,
            recommendations=recommendations,
            details={
                "run_mode": run_mode,
                "objective": objective,
                "min_items": min_items,
                "code_length": len(code),
            },
        )


# Backward-compatible alias: prefer runtime critic implementation.
try:
    from critic_runtime import Critic as Critic  # type: ignore
    from critic_runtime import CriticIssue as CriticIssue  # type: ignore
    from critic_runtime import CriticVerdict as CriticVerdict  # type: ignore
except Exception:
    pass
