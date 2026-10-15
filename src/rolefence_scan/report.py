"""Findings and the report. Every finding is written for an administrator, not a developer:
what was seen, why it matters, how to close it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

HIGH, MEDIUM, LOW, INFO = "high", "medium", "low", "info"
_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3}


@dataclass
class Finding:
    id: str            # stable short id, e.g. "open-tool-list"
    title: str
    severity: str      # high / medium / low / info
    what: str          # what the scanner saw, in plain words
    why: str           # why it matters
    fix: str           # how to close it
    evidence: dict = field(default_factory=dict)   # small, never values from the data


@dataclass
class Report:
    target: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    findings: list[Finding] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)     # checks that found nothing
    skipped: list[str] = field(default_factory=list)    # checks that could not run, with the reason

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def ok(self, check: str) -> None:
        self.passed.append(check)

    def skip(self, check: str, reason: str) -> None:
        self.skipped.append(f"{check}: {reason}")

    @property
    def worst(self) -> str:
        if not self.findings:
            return INFO
        return min((f.severity for f in self.findings), key=lambda s: _ORDER[s])

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: _ORDER[f.severity])

    def to_json(self) -> str:
        return json.dumps({
            "target": self.target,
            "started_at": self.started_at,
            "worst": self.worst,
            "findings": [asdict(f) for f in self.sorted_findings()],
            "passed": self.passed,
            "skipped": self.skipped,
        }, indent=2, default=str)

    def to_text(self) -> str:
        lines = [f"rolefence-scan report for {self.target}", f"run at {self.started_at}", ""]
        if not self.findings:
            lines.append("No problems found by the checks that ran.")
        else:
            counts = {s: sum(1 for f in self.findings if f.severity == s) for s in (HIGH, MEDIUM, LOW, INFO)}
            summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
            lines.append(f"{len(self.findings)} finding(s): {summary}")
        lines.append("")
        for i, f in enumerate(self.sorted_findings(), 1):
            lines.append(f"{i}. [{f.severity.upper()}] {f.title}")
            lines.append(f"   What we saw:    {f.what}")
            lines.append(f"   Why it matters: {f.why}")
            lines.append(f"   How to close:   {f.fix}")
            if f.evidence:
                lines.append(f"   Details:        {json.dumps(f.evidence, default=str)}")
            lines.append("")
        if self.passed:
            lines.append("Checks that passed: " + ", ".join(self.passed))
        if self.skipped:
            lines.append("Checks that could not run:")
            lines.extend(f"  - {s}" for s in self.skipped)
        return "\n".join(lines)
