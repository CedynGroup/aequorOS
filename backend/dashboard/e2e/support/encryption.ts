import { test as base, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  E2E_BASE_URL,
  E2E_KMS_STATE,
  E2E_KMS_UPSTREAM,
  E2E_TMP,
} from "../../playwright.config";
import { E2E_USERS, writeStorageState } from "./mint";

export type EncryptionStory = "revocation" | "outage" | "rotation";
type Action =
  | "create"
  | "connect"
  | "enable"
  | "disable"
  | "rotate"
  | "assert-storage"
  | "remove";
type Result = {
  status?: string;
  batch_id?: string;
  disabled_connect_refused?: boolean;
  encrypted_parties?: number;
  exact_identifier_matches?: number;
  party_ciphertexts?: string[];
};
const stories: EncryptionStory[] = ["revocation", "outage", "rotation"];
export function storyIdentity(story: EncryptionStory) {
  const role = `encryption_${story}`;
  return {
    role,
    org: E2E_USERS[role].organizationId!,
    bank: `BK-KEY0000${stories.indexOf(story) + 1}`,
  };
}
export function keyControl(story: EncryptionStory, action: Action): Result {
  if (!E2E_KMS_UPSTREAM)
    throw new Error(
      "Encryption journeys require BANK_KMS_ENDPOINT_URL; start the local encryption compose profile.",
    );
  const backend = path.resolve(__dirname, "../../..");
  return JSON.parse(
    execFileSync(
      path.join(backend, ".venv/bin/python"),
      [
        path.join(backend, "scripts/e2e_bank_encryption.py"),
        action,
        "--story",
        story,
        "--directory",
        E2E_TMP,
      ],
      {
        cwd: E2E_TMP,
        env: {
          ...process.env,
          PYTHONPATH: backend,
          APP_ENV: "test",
          TLS_ALLOW_PLAINTEXT: "1",
          BANK_KMS_ENDPOINT_URL: E2E_KMS_UPSTREAM,
          DATABASE_URL: "",
          WORKER_DATABASE_URL: "",
          RUN_INPROCESS_WORKER: "0",
        },
        encoding: "utf8",
        timeout: 60_000,
      },
    ).trim(),
  ) as Result;
}
export function keyService(available: boolean) {
  writeFileSync(E2E_KMS_STATE, available ? "available" : "outage");
}
export async function encryptionEvidence(page: Page, name: string) {
  const directory = process.env.E2E_EVIDENCE_DIR;
  if (!directory) return;
  mkdirSync(directory, { recursive: true });
  await page.screenshot({
    path: path.join(directory, `encryption-${name}.png`),
    fullPage: true,
  });
  if (name !== "revoked-draft") return;
  const viewport = page.viewportSize();
  const initialTheme = await page.locator("html").getAttribute("data-theme");
  try {
    for (const theme of ["light", "dark"]) {
      const toggle = page.getByRole("button", {
        name: `Switch to ${theme} theme`,
      });
      if (await toggle.count()) await toggle.click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.screenshot({
        path: path.join(directory, `encryption-${name}-${theme}-desktop.png`),
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({
        path: path.join(directory, `encryption-${name}-${theme}-mobile.png`),
        fullPage: true,
      });
      if (viewport) await page.setViewportSize(viewport);
    }
  } finally {
    if (viewport) await page.setViewportSize(viewport);
    if (initialTheme === "light" || initialTheme === "dark") {
      const restore = page.getByRole("button", {
        name: `Switch to ${initialTheme} theme`,
      });
      if (await restore.count()) await restore.click();
    }
  }
}

export const test = base.extend<{ encryptionStory: EncryptionStory }>({
  encryptionStory: ["revocation", { option: true }],
  storageState: async ({ encryptionStory }, provideState) => {
    try {
      keyControl(encryptionStory, "create");
      expect(keyControl(encryptionStory, "connect")).toEqual({
        status: "active",
        disabled_connect_refused: true,
      });
      const { role } = storyIdentity(encryptionStory);
      await provideState(await writeStorageState(role, E2E_BASE_URL, E2E_TMP));
    } finally {
      if (E2E_KMS_UPSTREAM) {
        keyService(true);
        keyControl(encryptionStory, "remove");
      }
    }
  },
});
export { expect };
