import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

test("Capital requests stay pending for branch grants and resolve with audited whole-institution authority", async ({
  request,
}) => {
  // A dedicated identity keeps authorization-version changes isolated from
  // other journeys that share the disposable database.
  const memberId = randomUUID();
  execFileSync(
    path.join(__dirname, "../../.venv/bin/python"),
    [
      "-c",
      `
import sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import User
with Session(create_engine(sys.argv[1])) as db:
    db.add(User(id=UUID(sys.argv[2]), organization_id='OR-DEM00001', email=f'capital-{sys.argv[2]}@example.test', display_name='Capital request member', role='viewer'))
    db.commit()
`,
      `sqlite+pysqlite:///${path.join(E2E_TMP, "e2e.db")}`,
      memberId,
    ],
    { cwd: path.join(__dirname, "../..") },
  );
  E2E_USERS.capital_request_member = {
    id: memberId,
    roles: ["viewer"],
    authv: 1,
  };
  const api = `${E2E_API_ORIGIN}/api/v1/authorization`;
  const owner = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const member = {
    Authorization: `Bearer ${await mintBackendToken("capital_request_member")}`,
  };
  const observations: unknown[] = [];
  const ids: string[] = [];
  for (const route of ["/basel", "/basel/rwa"]) {
    const response = await request.post(`${api}/access-requests`, {
      headers: member,
      data: {
        route,
        institution_id: "BK-SAMP0001",
        module_scope: "cap",
        sensitivity_scope: "aggregated",
        permission: "view",
        reason_category: "role_change",
      },
    });
    expect(response.status(), await response.text()).toBe(201);
    ids.push((await response.json()).id);
  }
  const grant = {
    principal_user_id: memberId,
    role_bundle: "viewer",
    institution_scope: "institution",
    institution_id: "BK-SAMP0001",
    module_scope: "cap",
    sensitivity_scope: "aggregated",
    reason_category: "role_change",
    data_scope_kind: "branch",
    data_scope_values: ["ACC"],
    reason_detail: "Capital reporting coverage",
    reference: "LIVE-CAP-REVIEW",
  };
  const preview = await request.post(`${api}/bindings/preview`, {
    headers: owner,
    data: grant,
  });
  expect(preview.status()).toBe(200);
  const reviewed = {
    ...grant,
    expected_authority_sentence: (await preview.json()).authority_sentence,
  };
  const { principal_user_id, ...approval } = reviewed;
  const refused = await request.post(
    `${api}/access-requests/${ids[0]}/approve`,
    { headers: owner, data: approval },
  );
  expect(refused.status()).toBe(409);
  observations.push({
    operation: "approve branch-only grant",
    status: refused.status(),
    body: await refused.json(),
  });
  const branch = await request.post(`${api}/bindings`, {
    headers: owner,
    data: reviewed,
  });
  expect(branch.status(), await branch.text()).toBe(201);
  const pending = await request.get(`${api}/access-requests`, {
    headers: owner,
  });
  expect(pending.status()).toBe(200);
  const pendingRows = (await pending.json()).requests.filter(
    (row: { id: string }) => ids.includes(row.id),
  );
  expect(pendingRows).toHaveLength(2);
  expect(
    pendingRows.every((row: { status: string }) => row.status === "pending"),
  ).toBe(true);
  observations.push({
    operation: "composer branch grant leaves requests pending",
    requests: pendingRows,
  });
  const whole = { ...grant, data_scope_kind: "all", data_scope_values: [] };
  const wholePreview = await request.post(`${api}/bindings/preview`, {
    headers: owner,
    data: whole,
  });
  expect(wholePreview.status()).toBe(200);
  const { principal_user_id: ignored, ...wholeApproval } = whole;
  const bindingIds: string[] = [];
  for (const id of ids) {
    const response = await request.post(`${api}/access-requests/${id}/approve`, {
      headers: owner,
      data: {
        ...wholeApproval,
        expected_authority_sentence: (await wholePreview.json())
          .authority_sentence,
      },
    });
    expect(response.status(), await response.text()).toBe(200);
    const body = await response.json();
    bindingIds.push(body.binding.id);
    observations.push({
      operation: "approve whole-institution authority",
      requestId: id,
      body,
    });
  }
  expect(bindingIds[0]).toBe(bindingIds[1]);
  const audits = JSON.parse(
    execFileSync(
      path.join(__dirname, "../../.venv/bin/python"),
      [
        "-c",
        `
import json, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    rows = db.execute("SELECT entity_id, details FROM audit_events WHERE event_type='authorization.access_request_approved'").fetchall()
    print(json.dumps([{ "request_id": rid, "details": json.loads(details) } for rid, details in rows if rid in sys.argv[2:]]))
`,
        path.join(E2E_TMP, "e2e.db"),
        ...ids,
      ],
      { encoding: "utf8" },
    ),
  );
  expect(audits).toHaveLength(2);
  for (const audit of audits) {
    expect(audit.details.reason_category).toBe(grant.reason_category);
    expect(audit.details.reason_detail).toBe(grant.reason_detail);
    expect(audit.details.reference).toBe(grant.reference);
    expect(audit.details.binding_id).toBe(bindingIds[0]);
  }
  observations.push({
    operation: "persisted decision audit including reused binding",
    audits,
  });
  if (process.env.E2E_EVIDENCE_DIR)
    writeFileSync(
      path.join(
        process.env.E2E_EVIDENCE_DIR,
        "capital-request-data-coverage.json",
      ),
      JSON.stringify(observations, null, 2),
    );
});
