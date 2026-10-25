import { expect, type APIRequestContext } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../../playwright.config";
import { biApi } from "./bi";
import { requireObjectStorage } from "./object-storage";

/** Persist a failed source attempt in an owned institution, without financial rows. */
export async function createFailedIngestion(
  request: APIRequestContext,
  bankId: string,
): Promise<string> {
  requireObjectStorage();
  const base = `/banks/${bankId}`;
  const mapping = await biApi(request, "analyst", `${base}/mapping-configs`, {
    method: "POST",
    data: {
      source_system: "EXCEL_CSV",
      name: "Failed source navigation",
      config: {},
      activate: true,
      reason: "Inspect a persisted source failure",
    },
  });
  expect(mapping.status, JSON.stringify(mapping.body)).toBe(200);
  const attempt = await biApi(request, "analyst", `${base}/ingestion-batches`, {
    method: "POST",
    data: {
      source_system: "EXCEL_CSV",
      as_of_date: new Date().toISOString().slice(0, 10),
      location: path.join(E2E_TMP, "missing-navigation-source.csv"),
      reason: "Inspect a missing source attempt",
    },
  });
  expect(attempt.status, JSON.stringify(attempt.body)).toBe(201);
  const { batch } = attempt.body as { batch: { id: string; status: string } };
  expect(batch.status).toBe("failed");
  return batch.id;
}
