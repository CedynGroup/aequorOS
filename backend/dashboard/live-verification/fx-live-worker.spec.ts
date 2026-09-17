import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_ORG_ID, E2E_USERS, mintBackendToken } from "../e2e/support/mint";

test("queued and scheduled workers persist authorized FX runs", async ({ request }) => {
  test.setTimeout(120_000);
  const api = `${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001`;
  const headers = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const periods = await (await request.get(`${api}/reporting-periods`, { headers })).json();
  const queued = await request.post(`${api}/official-runs`, {
    headers, data: { as_of_date: periods.periods[0].period_end, reason: "Live worker authorization" },
  });
  expect(queued.status(), await queued.text()).toBe(202);
  const queuedBody = await queued.json();
  const script = `
import json
from sqlalchemy import select
from app.db.session import get_worker_sessionmaker
from app.models import Job
from app.services import job_queue
from app.worker import run_once
with get_worker_sessionmaker()() as session:
    job_queue.enqueue(session, "${E2E_ORG_ID}", "scheduled_tick", coalesce_key="live-fx-tick")
    session.commit()
assert run_once(("scheduled_tick",))
with get_worker_sessionmaker()() as session:
    jobs = list(session.scalars(select(Job).where(Job.job_type == "official_run")))
    assert len(jobs) == 2, len(jobs)
    interactive = next(j for j in jobs if str(j.id) == "${queuedBody.job_id}")
    assert interactive.payload["actor_user_id"] == "${E2E_USERS.admin.id}"
    print(json.dumps({"queued_jobs": [{"id": str(j.id), "payload": j.payload, "coalesce_key": j.coalesce_key} for j in jobs]}))
assert run_once(("official_run",))
assert run_once(("official_run",))
with get_worker_sessionmaker()() as session:
    jobs = list(session.scalars(select(Job).where(Job.job_type == "official_run")))
    print(json.dumps({"completed_jobs": [{"id": str(j.id), "status": j.status, "payload": j.payload} for j in jobs]}))
    assert all(j.status == "succeeded" for j in jobs)
`;
  const output = execFileSync("uv", ["run", "python", "-c", script], {
    cwd: path.resolve(__dirname, "../.."), encoding: "utf8", timeout: 90_000,
    env: { ...process.env, DATABASE_URL: `sqlite+pysqlite:///${path.join(E2E_TMP, "e2e.db")}`, WORKER_DATABASE_URL: "", APP_ENV: "test", RUN_INPROCESS_WORKER: "0", OFFICIAL_RUN_ENABLED: "1", OFFICIAL_RUN_HOUR: "23" },
  });
  const response = await request.get(`${api}/regulatory-runs?module=fx&limit=100`, { headers });
  expect(response.status()).toBe(200);
  const runs = await response.json();
  expect(runs.total).toBeGreaterThan(0);
  expect(runs.runs.every((run: { status: string }) => run.status === "succeeded")).toBeTruthy();
  if (process.env.E2E_EVIDENCE_DIR) writeFileSync(path.join(process.env.E2E_EVIDENCE_DIR, "fx-live-worker.json"), JSON.stringify({ queued: queuedBody, worker: output, runs }, null, 2));
});
