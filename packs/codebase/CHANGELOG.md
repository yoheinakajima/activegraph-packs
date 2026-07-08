# Codebase Pack Changelog

## v0.1.2 — activegraph 1.3 compatibility (2026-07-08)

### Fixed
- `@tool` wrapper signatures satisfy the runtime's v1.3 registration-time
  validation: every parameter beyond the `(args, ctx)` invocation contract
  now has a default. No behavior change (behaviors call the `_fn`
  variants directly).

## v0.1.1 — Relation integrity fix (2026-07-08)

### Fixed
- **`add_relation` argument order.** Relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the same
  bug the Chat Pack fixed in its v0.2.0. Affected relations were being written
  as garbage edges (the type string as the source id), silently breaking graph
  traversal over this pack's audit trail. Part of a repo-wide sweep (80 calls
  across 14 packs) that also corrected fixture assertions written against the
  broken shape (`r.source` where `r.type` was meant).

## v0.1.0 — 2026-06-03

### Added
- `repo` object type: repository with full_name, language, stars, open issues
- `code_file` object type: file within a repo with path and language detection
- `code_function` object type: function/method with signature and docstring
- `dependency` object type: package dependency with vulnerability tracking
- `issue` object type: GitHub/GitLab issue with labels, state, and task link
- `pull_request` object type: PR with state, branch, and merge tracking
- `architecture_decision` object type: ADR with context/decision/consequences sections
- `code_change` object type: commit or PR diff summary
- `test_result` object type: test run with pass/fail/coverage stats
- Relation types: `file_in_repo`, `function_in_file`, `repo_depends_on`, `issue_in_repo`, `pr_in_repo`, `adr_in_repo`, `change_in_repo`, `test_for_repo`, `derived_from_source`
- `repo_ingester` behavior: ingests repo manifests and webhook repository events
- `issue_tracker` behavior: creates Issue + Core task from webhook issue events
- `adr_extractor` behavior: parses ADR markdown files at configured path patterns
- `change_summarizer` behavior: summarizes push/PR webhook events into CodeChange
- `dependency_auditor` behavior: creates Dependency objects with vulnerability flags
- Module-level registries with `clear_codebase_registry()` for fixture isolation
- Tools: `ingest_github_webhook`, `ingest_repo_file`, `create_repo`, `create_issue`
- Two fixtures covering repo/issue/ADR tracking and code change tracking

### Notes
- v0.1 dependency auditing reads pre-parsed metadata; package.json/pyproject.toml parsing in v0.2
- code_file and code_function objects created in v0.2 from repo file ingestion
