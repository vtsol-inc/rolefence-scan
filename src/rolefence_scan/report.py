"""Findings and the report. Every finding is written for an administrator, not a developer:
what was seen, why it matters, how to close it."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable

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
    # called with ("finding"|"ok"|"skip", payload) as checks finish; the browser wizard uses it
    on_event: Callable[[str, dict], None] | None = field(default=None, repr=False, compare=False)

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    def add(self, f: Finding) -> None:
        self.findings.append(f)
        self._emit("finding", asdict(f))

    def ok(self, check: str) -> None:
        self.passed.append(check)
        self._emit("ok", {"check": check})

    def skip(self, check: str, reason: str) -> None:
        self.skipped.append(f"{check}: {reason}")
        self._emit("skip", {"check": check, "reason": reason})

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

    def to_html(self) -> str:
        """A self-contained page: printable, mailable, no scripts."""
        e = html.escape
        colours = {HIGH: "#b42318", MEDIUM: "#b54708", LOW: "#175cd3", INFO: "#475467"}
        parts = [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            f"<title>rolefence-scan report</title>",
            "<style>body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;color:#101828}"
            "h1{font-size:22px;margin:0 0 4px}.muted{color:#475467}.card{border:1px solid #e4e7ec;border-radius:10px;padding:14px 16px;margin:14px 0}"
            ".sev{display:inline-block;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#fff;border-radius:6px;padding:2px 8px;margin-right:8px}"
            ".row{margin:6px 0}.row b{display:inline-block;min-width:130px;color:#475467;font-weight:600}code{font-size:13px;background:#f2f4f7;padding:1px 5px;border-radius:4px}"
            "@media print{.card{break-inside:avoid}}</style></head><body>",
            f"<h1>rolefence-scan report</h1><div class='muted'>Target: <code>{e(self.target)}</code> · run at {e(self.started_at)}</div>",
        ]
        if not self.findings:
            parts.append("<p>No problems found by the checks that ran.</p>")
        else:
            counts = {s: sum(1 for f in self.findings if f.severity == s) for s in (HIGH, MEDIUM, LOW, INFO)}
            summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
            parts.append(f"<p><strong>{len(self.findings)} finding(s):</strong> {e(summary)}</p>")
        for f in self.sorted_findings():
            parts.append(
                f"<div class='card'><div><span class='sev' style='background:{colours.get(f.severity, '#475467')}'>{e(f.severity)}</span>"
                f"<strong>{e(f.title)}</strong></div>"
                f"<div class='row'><b>What we saw</b>{e(f.what)}</div>"
                f"<div class='row'><b>Why it matters</b>{e(f.why)}</div>"
                f"<div class='row'><b>How to close it</b>{e(f.fix)}</div>"
                + (f"<div class='row'><b>Details</b><code>{e(json.dumps(f.evidence, default=str))}</code></div>" if f.evidence else "")
                + "</div>"
            )
        if self.passed:
            parts.append(f"<p class='muted'><strong>Checks that passed:</strong> {e(', '.join(self.passed))}</p>")
        if self.skipped:
            parts.append("<p class='muted'><strong>Checks that could not run:</strong></p><ul class='muted'>"
                         + "".join(f"<li>{e(s)}</li>" for s in self.skipped) + "</ul>")
        parts.append("<p class='muted'>Generated by rolefence-scan · <a href='https://rolefence.com'>rolefence.com</a></p></body></html>")
        return "".join(parts)
