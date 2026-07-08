"""Codebase Pack behaviors — v0.1.

Behaviors:
  repo_ingester      — source.created (kind=github_webhook/repo_file) → Repo
  issue_tracker      — source.created (webhook event=issues or kind=issue) → Issue + Core task
  adr_extractor      — source.created (kind=repo_file, ADR path pattern) → ArchitectureDecision
  change_summarizer  — source.created (kind=github_push/pull_request) → CodeChange
  dependency_auditor — repo.created → Dependency objects from metadata

Registries:
  _REPO_REGISTRY: full_name → repo_id
  _ISSUE_REGISTRY: "full_name#number" → issue_id
  Call clear_codebase_registry() between test fixtures.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from activegraph.packs import behavior

from .settings import CodebaseSettings

_REPO_REGISTRY: dict[str, str] = {}
_ISSUE_REGISTRY: dict[str, str] = {}


def clear_codebase_registry() -> None:
    _REPO_REGISTRY.clear()
    _ISSUE_REGISTRY.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_language(filename: str) -> Optional[str]:
    EXT_MAP = {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".go": "go",
        ".rs": "rust", ".java": "java", ".cs": "csharp",
        ".rb": "ruby", ".php": "php", ".cpp": "cpp", ".c": "c",
        ".md": "markdown", ".yaml": "yaml", ".yml": "yaml",
        ".json": "json", ".toml": "toml",
    }
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_MAP.get(ext)


def _is_adr_path(path: str, patterns: list[str]) -> bool:
    path_lower = path.lower()
    return any(pattern.lower() in path_lower for pattern in patterns)


def _parse_adr_content(content: str) -> dict:
    """Parse markdown ADR into context/decision/consequences sections."""
    sections = {"context": "", "decision": "", "consequences": ""}
    current = None
    lines = content.split("\n")
    for line in lines:
        lower = line.lower().strip()
        if "## context" in lower or "## background" in lower:
            current = "context"
        elif "## decision" in lower:
            current = "decision"
        elif "## consequences" in lower or "## result" in lower:
            current = "consequences"
        elif current and line.strip():
            sections[current] += line + "\n"
    return {k: v.strip() for k, v in sections.items()}


def _mock_adr_number(repo_id: str) -> int:
    """Generate incrementing ADR number per repo."""
    count = sum(1 for k in _ISSUE_REGISTRY if k.startswith(f"adr:{repo_id}:"))
    return count + 1


@behavior(
    name="repo_ingester",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["repo"],
)
def repo_ingester(event, graph, ctx, *, settings: CodebaseSettings):
    """Ingest a repository source into a Repo object.

    On: object.created (source, kind in {github_webhook, repo_file, repo_manifest})
    Creates: repo
    Relations: derived_from_source

    Handles: GitHub webhook repository events and direct repo manifest sources.
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    data = obj.get("data", {})

    kind = data.get("kind") or ""
    if kind not in ("github_webhook", "repo_file", "repo_manifest"):
        return

    meta = data.get("metadata") or {}
    repo_data = meta.get("repository") or meta.get("repo") or {}
    full_name = repo_data.get("full_name") or meta.get("full_name") or ""

    if not full_name:
        return

    if full_name in _REPO_REGISTRY:
        return

    name = full_name.split("/")[-1] if "/" in full_name else full_name
    language = repo_data.get("language") or meta.get("language")
    description = repo_data.get("description") or ""
    url = repo_data.get("html_url") or repo_data.get("url") or meta.get("url")
    stars = repo_data.get("stargazers_count") or repo_data.get("stars") or 0
    default_branch = repo_data.get("default_branch") or "main"

    try:
        repo = graph.add_object("repo", {
            "name": name,
            "full_name": full_name,
            "description": description,
            "url": url,
            "default_branch": default_branch,
            "language": language,
            "stars": stars,
            "source_id": source_id,
        })
        _REPO_REGISTRY[full_name] = repo.id
        graph.add_relation(repo.id, source_id, "derived_from_source")
    except Exception:
        pass


@behavior(
    name="issue_tracker",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["issue", "task"],
)
def issue_tracker(event, graph, ctx, *, settings: CodebaseSettings):
    """Create Issue objects from GitHub webhook events or plain issue sources.

    On: object.created (source, kind=github_webhook+event=issues OR kind=issue)
    Creates: issue, task (if auto_create_issues_as_tasks=True)
    Relations: issue_in_repo, action_creates_task
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    data = obj.get("data", {})

    kind = data.get("kind") or ""
    meta = data.get("metadata") or {}

    is_issue_source = kind == "issue" or (
        kind == "github_webhook" and meta.get("event") in ("issues", "issue")
    )
    if not is_issue_source:
        return

    issue_data = meta.get("issue") or meta
    repo_data = meta.get("repository") or {}
    full_name = repo_data.get("full_name") or meta.get("repo_full_name") or "unknown/unknown"

    issue_number = int(issue_data.get("number") or issue_data.get("issue_number") or 0)
    issue_key = f"{full_name}#{issue_number}"

    if issue_key in _ISSUE_REGISTRY:
        return

    title = issue_data.get("title") or data.get("content", "")[:80] or "Untitled Issue"
    body = issue_data.get("body") or data.get("content") or ""
    state = issue_data.get("state") or "open"
    labels = [l.get("name") if isinstance(l, dict) else str(l)
              for l in (issue_data.get("labels") or [])]
    author_ref = (issue_data.get("user") or {}).get("login") or issue_data.get("author_ref")
    created_at = issue_data.get("created_at") or _now_iso()

    repo_id = _REPO_REGISTRY.get(full_name)

    if not repo_id:
        try:
            repo = graph.add_object("repo", {
                "name": full_name.split("/")[-1],
                "full_name": full_name,
                "source_id": source_id,
            })
            repo_id = repo.id
            _REPO_REGISTRY[full_name] = repo_id
        except Exception:
            return

    task_id = None
    if settings.auto_create_issues_as_tasks and state == "open":
        try:
            task = graph.add_object("task", {
                "title": f"[Issue] {title}",
                "description": body[:500],
                "status": "candidate",
                "priority": "high" if "bug" in [l.lower() for l in labels] else "medium",
                "owner_ref": author_ref,
            })
            task_id = task.id
        except Exception:
            pass

    try:
        issue = graph.add_object("issue", {
            "repo_id": repo_id,
            "issue_number": issue_number,
            "title": title,
            "body": body[:2000],
            "state": state,
            "labels": labels,
            "author_ref": author_ref,
            "created_at": created_at,
            "source_id": source_id,
            "task_id": task_id,
        })
        _ISSUE_REGISTRY[issue_key] = issue.id
        graph.add_relation(issue.id, repo_id, "issue_in_repo")
        graph.add_relation(issue.id, source_id, "derived_from_source")
        if task_id:
            try:
                graph.add_relation(issue.id, task_id, "action_creates_task")
            except Exception:
                pass
    except Exception:
        pass


@behavior(
    name="adr_extractor",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["architecture_decision"],
)
def adr_extractor(event, graph, ctx, *, settings: CodebaseSettings):
    """Extract Architecture Decision Records from repo file sources.

    On: object.created (source, kind=repo_file, path matches ADR pattern)
    Creates: architecture_decision
    Relations: adr_in_repo, derived_from_source

    Handles markdown ADR files with standard sections:
    ## Context / ## Decision / ## Consequences
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    data = obj.get("data", {})

    if data.get("kind") != "repo_file":
        return

    meta = data.get("metadata") or {}
    path = meta.get("path") or meta.get("file_path") or ""

    if not _is_adr_path(path, settings.adr_path_patterns):
        return

    content = data.get("content") or ""
    full_name = meta.get("repo_full_name") or meta.get("full_name") or ""
    repo_id = _REPO_REGISTRY.get(full_name)

    title_match = re.search(r"^#\s*(.+)$", content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.split("/")[-1].replace("-", " ").title()

    adr_num_match = re.search(r"(\d+)", path.split("/")[-1])
    adr_number = int(adr_num_match.group(1)) if adr_num_match else None

    status = "accepted"
    content_lower = content.lower()
    if "deprecated" in content_lower:
        status = "deprecated"
    elif "proposed" in content_lower:
        status = "proposed"
    elif "superseded" in content_lower:
        status = "superseded"

    sections = _parse_adr_content(content)

    if not repo_id and full_name:
        try:
            repo = graph.add_object("repo", {
                "name": full_name.split("/")[-1],
                "full_name": full_name,
                "source_id": source_id,
            })
            repo_id = repo.id
            _REPO_REGISTRY[full_name] = repo_id
        except Exception:
            pass

    try:
        adr = graph.add_object("architecture_decision", {
            "repo_id": repo_id or "",
            "title": title,
            "context": sections["context"][:1000],
            "decision": sections["decision"][:1000],
            "consequences": sections["consequences"][:500],
            "status": status,
            "adr_number": adr_number,
            "source_id": source_id,
        })
        graph.add_relation(adr.id, source_id, "derived_from_source")
        if repo_id:
            graph.add_relation(adr.id, repo_id, "adr_in_repo")
    except Exception:
        pass


@behavior(
    name="change_summarizer",
    on=["object.created"],
    where={"object.type": "source"},
    creates=["code_change"],
)
def change_summarizer(event, graph, ctx, *, settings: CodebaseSettings):
    """Summarize code changes from push/PR webhook sources.

    On: object.created (source, kind in {github_webhook, github_push, pull_request})
    Creates: code_change
    Relations: change_in_repo, derived_from_source
    """
    obj = event.payload.get("object", {})
    source_id = obj.get("id")
    data = obj.get("data", {})

    kind = data.get("kind") or ""
    meta = data.get("metadata") or {}
    event_type = meta.get("event") or ""

    is_change_source = kind in ("github_push", "code_change") or (
        kind == "github_webhook" and event_type in ("push", "pull_request")
    )
    if not is_change_source:
        return

    repo_data = meta.get("repository") or {}
    full_name = repo_data.get("full_name") or meta.get("repo_full_name") or "unknown/unknown"
    repo_id = _REPO_REGISTRY.get(full_name)

    commits = meta.get("commits") or []
    pr_data = meta.get("pull_request") or {}

    files_changed = []
    lines_added = 0
    lines_removed = 0

    for commit in commits:
        files_changed.extend(commit.get("added") or [])
        files_changed.extend(commit.get("modified") or [])
        files_changed.extend(commit.get("removed") or [])

    if pr_data:
        lines_added = pr_data.get("additions") or 0
        lines_removed = pr_data.get("deletions") or 0

    commit_sha = meta.get("after") or (commits[0].get("id") if commits else None)
    author = meta.get("pusher", {}).get("name") or meta.get("sender", {}).get("login")

    summary_parts = []
    if commits:
        msgs = [c.get("message", "")[:60] for c in commits[:3]]
        summary_parts.append(f"{len(commits)} commit(s): " + "; ".join(msgs))
    elif pr_data:
        summary_parts.append(f"PR: {pr_data.get('title', '')[:80]}")
    summary = " | ".join(summary_parts) or "Code change"

    if not repo_id and full_name:
        try:
            repo = graph.add_object("repo", {
                "name": full_name.split("/")[-1],
                "full_name": full_name,
                "source_id": source_id,
            })
            repo_id = repo.id
            _REPO_REGISTRY[full_name] = repo_id
        except Exception:
            pass

    try:
        change = graph.add_object("code_change", {
            "repo_id": repo_id or "",
            "summary": summary[:500],
            "files_changed": list(set(files_changed))[:50],
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "commit_sha": commit_sha,
            "author_ref": author,
            "source_id": source_id,
        })
        graph.add_relation(change.id, source_id, "derived_from_source")
        if repo_id:
            graph.add_relation(change.id, repo_id, "change_in_repo")
    except Exception:
        pass


@behavior(
    name="dependency_auditor",
    on=["object.created"],
    where={"object.type": "repo"},
    creates=["dependency"],
)
def dependency_auditor(event, graph, ctx, *, settings: CodebaseSettings):
    """Create Dependency objects from repo metadata.

    On: object.created (repo)
    Creates: dependency objects (from repo metadata.dependencies list)
    Relations: repo_depends_on

    v0.1: reads pre-parsed dependency list from repo source metadata.
    Real dependency parsing from package.json/pyproject.toml in v0.2.
    """
    obj = event.payload.get("object", {})
    repo_id = obj.get("id")
    data = obj.get("data", {})

    source_id = data.get("source_id")
    if not source_id:
        return

    try:
        source = graph.get_object(source_id)
        if not source:
            return
        meta = source.data.get("metadata") or {}
        deps = meta.get("dependencies") or []
    except Exception:
        return

    sev_threshold_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    min_sev = sev_threshold_order.get(settings.vulnerability_severity_threshold, 1)

    for dep in deps[:100]:
        if not isinstance(dep, dict):
            continue
        dep_name = dep.get("name") or ""
        if not dep_name:
            continue

        vuln = dep.get("vulnerability") or {}
        has_vuln = bool(vuln)
        vuln_sev = vuln.get("severity") or "low"
        vuln_meets_threshold = sev_threshold_order.get(vuln_sev, 0) >= min_sev

        try:
            dep_obj = graph.add_object("dependency", {
                "repo_id": repo_id,
                "name": dep_name,
                "version": dep.get("version"),
                "kind": dep.get("kind") or "direct",
                "ecosystem": dep.get("ecosystem") or "",
                "has_known_vulnerability": has_vuln and vuln_meets_threshold,
                "vulnerability_summary": vuln.get("summary") or "",
            })
            graph.add_relation(repo_id, dep_obj.id, "repo_depends_on")
        except Exception:
            pass


BEHAVIORS = [
    repo_ingester,
    issue_tracker,
    adr_extractor,
    change_summarizer,
    dependency_auditor,
]
