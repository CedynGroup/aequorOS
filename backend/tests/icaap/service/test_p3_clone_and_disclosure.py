"""¶74 revisions and updates, the post-freeze send-back, and the ¶82 disclosure.

This module's other-suite cases live in ``test_p3_clone_and_disclosure_integration.py``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.canonical_book_baseline


class TestSupervisoryRedaction:
    def test_a_supervisory_figure_can_never_be_published(self) -> None:
        """Pure: no code path emits a never-public block into a disclosure."""
        from app.icaap.domain import disclosure as domain  # noqa: PLC0415

        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "The add-on is "},
                        {
                            "type": "factRef",
                            "attrs": {"blockId": "block-1", "factKey": "supervisory_addon_total"},
                        },
                    ],
                },
                {"type": "dataBlock", "attrs": {"blockId": "block-1"}},
            ],
        }
        result = domain.redact_section(
            "capital_adequacy",
            doc,
            never_public={"block-1": ("supervisory_addons", "supervisory_addons")},
        )
        assert len(result.withheld) == 2
        assert not domain.contains_never_public(result.doc, ["block-1"])
        assert domain.WITHHELD_TEXT in str(result.doc)
