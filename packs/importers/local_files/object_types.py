"""Local Files importer contributes no graph types of its own.

The importer writes the activity-normalizer's provider-neutral
``acquired_item``, ``acquired_content``, ``backfill_cursor``, and
``ingestion_failure`` objects. Keeping those schemas in one owning pack is
what lets every importer remain a small format adapter.
"""

from __future__ import annotations

OBJECT_TYPES = []
RELATION_TYPES = []
