"""P5 subject profile: promoted facts about people, never agent identity."""

from activegraph.packs import Pack

from .behaviors import BEHAVIORS
from .object_types import OBJECT_TYPES, RELATION_TYPES
from .settings import SubjectProfileSettings
from .tools import TOOLS

# requires=["activity_normalizer"], integrates_with=["semantic_extraction", "entity", "memory_gateway"]
pack = Pack(
    name="subject_profile", version="0.3.0",
    description=(
        "Evidence-backed, explicitly reviewed subject facts with contradiction "
        "and supersession lifecycles, the owner alias-set projection, and "
        "importance seeding from confirmed facts."
    ),
    object_types=OBJECT_TYPES, relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS, tools=TOOLS, policies=(), prompts=(),
    settings_schema=SubjectProfileSettings,
)

__all__ = ["pack", "SubjectProfileSettings"]

