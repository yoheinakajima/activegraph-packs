"""activegraph.packs.codebase — Codebase Pack v0.1.

Repository, file, issue, PR, and architecture decision tracking.

Object types:
  repo                  — Code repository
  code_file             — File within a repo
  code_function         — Function or method within a file
  dependency            — Package dependency with vulnerability tracking
  issue                 — GitHub/GitLab issue
  pull_request          — Pull request
  architecture_decision — Architecture Decision Record (ADR)
  code_change           — Commit or PR diff summary
  test_result           — Test run result

Behaviors:
  repo_ingester      — source.created (kind=github_webhook/repo_manifest) → Repo
  issue_tracker      — source.created (webhook issue event) → Issue + Core task
  adr_extractor      — source.created (kind=repo_file, ADR path) → ArchitectureDecision
  change_summarizer  — source.created (push/PR webhook) → CodeChange
  dependency_auditor — repo.created → Dependency objects from metadata

Composes with: Core Pack (tasks from issues), Identity Pack (author resolution)
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import CodebaseSettings
from .tools import TOOLS

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# requires=["core"], composes_with=["identity_auth", "team_ops"]
pack = Pack(
    name="codebase",
    version="0.1.2",
    description=(
        "Codebase tracking: repo ingestion, issue/PR tracking, ADR extraction, "
        "code change summarization, dependency auditing. "
        "Provides 9 object types for engineering workflow visibility."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=(),
    prompts=load_prompts_from_dir(_PROMPTS_DIR) if _PROMPTS_DIR.exists() else (),
    settings_schema=CodebaseSettings,
)

__all__ = ["pack", "CodebaseSettings"]
