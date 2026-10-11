"""ICAAP framework registry: the regulator's instrument as versioned data."""

from __future__ import annotations

from app.icaap.domain.frameworks.rebase import (
    RebaseNotPossible,
    RebasePlan,
    SectionMove,
    plan_rebase,
)
from app.icaap.domain.frameworks.registry import (
    FrameworkNotFound,
    all_frameworks,
    applicable,
    framework_digest,
    get,
    load_all,
    reset_cache,
    versions,
)
from app.icaap.domain.frameworks.schema import (
    AttachmentRequirement,
    Citation,
    Framework,
    FrameworkSchemaError,
    RequirementItem,
    RiskCategoryDef,
    SectionDef,
    StageTemplate,
    parse_framework,
)

__all__ = [
    "AttachmentRequirement",
    "Citation",
    "Framework",
    "FrameworkNotFound",
    "FrameworkSchemaError",
    "RebaseNotPossible",
    "RebasePlan",
    "RequirementItem",
    "RiskCategoryDef",
    "SectionDef",
    "SectionMove",
    "StageTemplate",
    "all_frameworks",
    "applicable",
    "framework_digest",
    "get",
    "load_all",
    "parse_framework",
    "plan_rebase",
    "reset_cache",
    "versions",
]
