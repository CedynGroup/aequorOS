"""The family hooks, the ICAAP validation rules, the review chain templates,
and the two guards on the non-production framework directory.

This module's other-suite cases live in ``test_p3_hooks_and_rules_integration.py``.
"""

from __future__ import annotations

import pytest

from app.core.config import IcaapSettings
from tests.icaap.service.p3_support import (
    FRAMEWORK_ROOT,
)


class TestTheExtraFrameworkDirectory:
    def test_a_deployed_environment_refuses_to_load_an_unreviewed_framework(self) -> None:
        """An allow-list, not "not production": staging is a deployment too."""
        settings = IcaapSettings.model_validate({"ICAAP_EXTRA_FRAMEWORKS_DIR": str(FRAMEWORK_ROOT)})
        for env in ("production", "staging"):
            with pytest.raises(ValueError, match="ICAAP_EXTRA_FRAMEWORKS_DIR"):
                settings.extra_framework_roots(env)
        assert settings.extra_framework_roots("local") == (FRAMEWORK_ROOT,)
        assert settings.extra_framework_roots("test") == (FRAMEWORK_ROOT,)

    def test_an_unset_directory_is_no_directory_in_every_environment(self) -> None:
        settings = IcaapSettings.model_validate({"ICAAP_EXTRA_FRAMEWORKS_DIR": None})
        assert settings.extra_framework_roots("production") == ()
        blank = IcaapSettings.model_validate({"ICAAP_EXTRA_FRAMEWORKS_DIR": "  "})
        assert blank.extra_framework_roots("production") == ()
