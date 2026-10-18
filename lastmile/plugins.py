"""Pluggable agents: attach extra reviewers to the pipeline without touching its code.

A plugin looks at the facts / drafts at one or more *stages* and returns Issues. It can only
ADD findings (block or warn); it cannot edit drafts, cannot approve and cannot publish.

    stage "post_facts"  after intake, place resolution and level rules, before any drafting
    stage "post_draft"  after localization + verification, before the run becomes ready/blocked

Ways to attach one (all configured on the SERVER, never by API callers, so no SSRF surface):
    * a Python class implementing `Plugin`
    * `HttpPlugin(url)`  – any service (a Cloud Run service, an A2A/ADK agent behind an adapter, a
                           regional rule engine…) that speaks the small JSON contract below
    * env var  LASTMILE_PLUGINS="issuer,http:https://rumours.example/inspect;post_facts+post_draft;critical"

HTTP contract:  POST {url}   {"stage","pack_id","source_text","facts","derived","drafts","msg_type"}
                → 200 {"issues":[{"code","severity":"block|warn","message","lang"?}]}
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .checks import norm_text
from .packs import Pack
from .schemas import Derived, Facts, Issue, LangDraft

STAGES = ("post_facts", "post_draft")


@dataclass
class PluginContext:
    stage: str
    pack: Pack
    source_text: str
    facts: Facts
    derived: Derived
    drafts: dict[str, LangDraft] = field(default_factory=dict)
    msg_type: str = "Alert"


class Plugin(Protocol):
    name: str
    stages: tuple[str, ...]
    critical: bool  # if True, an unreachable/erroring plugin BLOCKS the run (fail-closed)

    def inspect(self, ctx: PluginContext) -> list[Issue]: ...


class IssuerPlugin:
    """Does the bulletin mention the authority the pack expects? A cheap provenance sanity check."""

    name = "issuer"
    stages = ("post_facts",)
    critical = False

    def inspect(self, ctx: PluginContext) -> list[Issue]:
        issuer = ctx.pack.hazard.issuer.strip()
        if not issuer:
            return []
        m = re.search(r"\(([A-Z][A-Z0-9]{1,9})\)", issuer)
        needles = [m.group(1)] if m else []
        needles.append(issuer)
        text = norm_text(ctx.source_text)
        if any(norm_text(n) in text for n in needles):
            return []
        return [Issue(code="issuer_not_in_source", severity="warn", source="plugin",
                      message=f"The bulletin does not mention the expected issuer ({needles[0]}). "
                              "Confirm it is an official bulletin.")]


class HttpPlugin:
    def __init__(self, url: str, name: Optional[str] = None, stages: tuple[str, ...] = ("post_draft",),
                 timeout: float = 5.0, critical: bool = False) -> None:
        if not re.match(r"^https?://", url):
            raise ValueError("plugin url must start with http:// or https://")
        bad = [s for s in stages if s not in STAGES]
        if bad:
            raise ValueError(f"unknown stage(s) {bad}; choose from {STAGES}")
        self.url, self.stages, self.timeout, self.critical = url, tuple(stages), timeout, critical
        self.name = name or "http:" + re.sub(r"^https?://", "", url).split("/")[0]

    def inspect(self, ctx: PluginContext) -> list[Issue]:
        payload = {
            "stage": ctx.stage, "pack_id": ctx.pack.id, "source_text": ctx.source_text,
            "facts": ctx.facts.model_dump(), "derived": ctx.derived.model_dump(),
            "drafts": {k: v.model_dump() for k, v in ctx.drafts.items()}, "msg_type": ctx.msg_type,
        }
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 — operator-configured URL
            body = json.loads(resp.read(1_000_000).decode("utf-8"))
        if not isinstance(body, dict) or not isinstance(body.get("issues", []), list):
            raise ValueError("plugin response must be {\"issues\": [...]}")
        out = []
        for i in body.get("issues", []):
            if not isinstance(i, dict):
                raise ValueError("each issue must be an object")
            out.append(Issue(code=str(i.get("code", "plugin_finding"))[:60],
                             severity="block" if i.get("severity") == "block" else "warn",
                             message=str(i.get("message", ""))[:500], lang=i.get("lang"), source="plugin"))
        return out


def run_plugins(plugins: list[Plugin], ctx: PluginContext) -> tuple[list[Issue], list[str]]:
    """Run every plugin registered for ctx.stage. Failures are findings, never crashes."""
    issues: list[Issue] = []
    notes: list[str] = []
    for p in plugins:
        if ctx.stage not in p.stages:
            continue
        try:
            found = p.inspect(ctx)
        except Exception as e:  # noqa: BLE001 — a misbehaving plugin must never crash the pipeline
            issues.append(Issue(
                code="plugin_unavailable", source="plugin", stage=ctx.stage,
                severity="block" if p.critical else "warn",
                message=f"Plugin '{p.name}' failed ({type(e).__name__}: {e})"
                        + (" — it is marked critical, so the run is blocked." if p.critical else "; continuing without it.")))
            notes.append(f"{p.name}: unavailable")
            continue
        for i in found:
            issues.append(i.model_copy(update={"source": "plugin", "stage": ctx.stage,
                                               "message": f"[{p.name}] {i.message}"}))
        notes.append(f"{p.name}: {len(found)} finding(s)")
    return issues, notes


def from_spec(spec: str) -> list[Plugin]:
    """Parse LASTMILE_PLUGINS, e.g. "issuer,http:https://x.example/inspect;post_facts,post_draft;critical"."""
    plugins: list[Plugin] = []
    for raw in (spec or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if item == "issuer":
            plugins.append(IssuerPlugin())
        elif item.startswith("http:"):
            parts = item[5:].split(";")
            url, opts = parts[0], parts[1:]
            stages = tuple(s.strip() for s in opts[0].split("+")) if opts and opts[0] not in ("", "critical") else ("post_draft",)
            plugins.append(HttpPlugin(url, stages=stages, critical="critical" in opts))
        else:
            raise ValueError(f"unknown plugin spec {item!r}")
    return plugins
