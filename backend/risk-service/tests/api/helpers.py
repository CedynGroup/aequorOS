from __future__ import annotations

from uuid import UUID

ORG_1 = UUID("11111111-1111-4111-8111-111111111111")
ORG_2 = UUID("22222222-2222-4222-8222-222222222222")
USER_1 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def headers(org_id: UUID = ORG_1, user_id: UUID | None = None) -> dict[str, str]:
    if user_id is None:
        user_id = USER_2 if org_id == ORG_2 else USER_1
    return {"X-Org-Id": str(org_id), "X-User-Id": str(user_id)}
