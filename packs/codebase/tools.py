"""Codebase Pack tools — v0.1."""

from __future__ import annotations

from typing import Any

from activegraph import Graph
from activegraph.packs import tool


def ingest_github_webhook_fn(
    graph: Graph,
    event: str,
    payload: dict[str, Any],
) -> object:
    """Create a source from a GitHub webhook payload, triggering codebase behaviors."""
    content = str(payload)[:2000]
    return graph.add_object("source", {
        "kind": "github_webhook",
        "content": content,
        "channel": "github",
        "metadata": {"event": event, **payload},
    })


def ingest_repo_file_fn(
    graph: Graph,
    repo_full_name: str,
    path: str,
    content: str,
    language: str | None = None,
) -> object:
    """Create a source for a repo file, triggering adr_extractor if applicable."""
    return graph.add_object("source", {
        "kind": "repo_file",
        "content": content,
        "channel": "github",
        "metadata": {
            "repo_full_name": repo_full_name,
            "path": path,
            "language": language or "",
            "repository": {"full_name": repo_full_name},
        },
    })


def create_repo_fn(
    graph: Graph,
    full_name: str,
    description: str = "",
    language: str | None = None,
    url: str | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> object:
    """Create a Repo source object (repo_manifest) to trigger repo_ingester."""
    return graph.add_object("source", {
        "kind": "repo_manifest",
        "content": description,
        "channel": "github",
        "metadata": {
            "full_name": full_name,
            "repository": {
                "full_name": full_name,
                "description": description,
                "language": language,
                "html_url": url or f"https://github.com/{full_name}",
            },
            "dependencies": dependencies or [],
        },
    })


def create_issue_fn(
    graph: Graph,
    repo_full_name: str,
    issue_number: int,
    title: str,
    body: str = "",
    state: str = "open",
    labels: list[str] | None = None,
    author_ref: str | None = None,
) -> object:
    """Create a source for a GitHub issue."""
    return graph.add_object("source", {
        "kind": "issue",
        "content": f"{title}\n\n{body}",
        "channel": "github",
        "metadata": {
            "event": "issues",
            "repo_full_name": repo_full_name,
            "repository": {"full_name": repo_full_name},
            "number": issue_number,
            "issue_number": issue_number,
            "title": title,
            "body": body,
            "state": state,
            "labels": [{"name": l} for l in (labels or [])],
            "author_ref": author_ref,
        },
    })


@tool(name="ingest_github_webhook", description="Ingest a GitHub webhook event payload.")
def ingest_github_webhook(graph: Graph, event: str, payload: dict[str, Any] = None) -> object:
    return ingest_github_webhook_fn(graph, event, payload)


@tool(name="ingest_repo_file", description="Ingest a repo file (e.g. ADR markdown) into the graph.")
def ingest_repo_file(
    graph: Graph, repo_full_name: str, path: str = "", content: str = "", language: str | None = None
) -> object:
    return ingest_repo_file_fn(graph, repo_full_name, path, content, language)


@tool(name="create_repo", description="Create a repo manifest source to trigger repo ingestion.")
def create_repo(
    graph: Graph, full_name: str, description: str = "",
    language: str | None = None, url: str | None = None,
    dependencies: list[dict[str, Any]] | None = None,
) -> object:
    return create_repo_fn(graph, full_name, description, language, url, dependencies)


@tool(name="create_issue", description="Create an issue source to trigger issue_tracker.")
def create_issue(
    graph: Graph, repo_full_name: str, issue_number: int = None, title: str = "",
    body: str = "", state: str = "open", labels: list[str] | None = None,
    author_ref: str | None = None,
) -> object:
    return create_issue_fn(graph, repo_full_name, issue_number, title, body, state, labels, author_ref)


TOOLS = [ingest_github_webhook, ingest_repo_file, create_repo, create_issue]
