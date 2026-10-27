import { test as base, expect } from "@playwright/test";
import { decode } from "@auth/core/jwt";
import { AccessRequestListReadFromJSON } from "@aequoros/risk-service-api";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from "../../playwright.config";
import {
  E2E_AUTH_SECRET,
  E2E_ORG_ID,
  E2E_USERS,
  mintBackendToken,
  writeStorageState,
} from "./mint";
import { requireObjectStorage } from "./object-storage";

type Identity = (typeof E2E_USERS)[string];
type Actor = {
  role: string;
  user_id: string;
  organization_id: string;
  email: string;
  display_name: string;
  scalar_role: string;
  authorization_version: number;
};
type Configuration = {
  roles: string[];
  ownedTenant?: boolean;
  signing?: boolean;
  branchRegister?: boolean;
  ownerReadModules?: string[];
  institutionType?: string;
  bindings?: {
    role?: string;
    bundle: "viewer" | "analyst" | "approver";
    module: string;
    sensitivity: string;
    branches?: string[];
  }[];
};
type Story = { organizationId: string; bankId: string; actors: Actor[] };
const BACKEND = path.resolve(__dirname, "../../..");
const PYTHON = path.join(BACKEND, ".venv/bin/python");
const DATABASE = path.join(E2E_TMP, "e2e.db");
const execute = promisify(execFile);

async function fixture(
  action: "provision" | "retire",
  request: object,
): Promise<string> {
  // Keep HTTP socket-close events flowing while Python works on the fixture.
  const { stdout } = await execute(
    PYTHON,
    [
      "scripts/e2e_story_identities.py",
      action,
      DATABASE,
      JSON.stringify(request),
    ],
    {
      cwd: BACKEND,
      encoding: "utf8",
      env: {
        ...process.env,
        PYTHONPATH: BACKEND,
        DATABASE_URL: `sqlite+pysqlite:///${DATABASE}`,
        WORKER_DATABASE_URL: "",
        APP_ENV: "test",
        TLS_ALLOW_PLAINTEXT: "1",
        SIGNER_ID_PEPPER: "e2e-signer-pepper-not-production-000",
        ATTESTATION_SIGNING_ENABLED: "1",
        SIGNING_BACKEND: "software",
        SIGNING_SOFTWARE_KEY_DIR: path.join(E2E_TMP, "signing-keys"),
        CREDENTIAL_VAULT_MASTER_KEY: Buffer.from(
          "e2e-vault-master-key-not-prod-00000",
        ).toString("base64"),
      },
    },
  );
  return stdout;
}

async function post(bank: string, route: string, data: object | FormData) {
  const response = await fetch(
    `${E2E_API_ORIGIN}/api/v1/banks/${bank}${route}`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${await mintBackendToken("analyst")}`,
        ...(data instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
      },
      body: data instanceof FormData ? data : JSON.stringify(data),
    },
  );
  const body: unknown = await response.json();
  expect(response.ok, JSON.stringify(body)).toBeTruthy();
  return body;
}

/** The bank declares its hierarchy through Data Engine; setup writes no canonical rows. */
async function prepareBranches(bank: string): Promise<void> {
  requireObjectStorage();
  const directory = mkdtempSync(
    path.join(tmpdir(), "aequoros-story-register-"),
  );
  const file = path.join(directory, "business-units.xlsx");
  try {
    await execute(PYTHON, [
      "-c",
      `
import sys
from openpyxl import Workbook
book = Workbook()
sheet = book.active
sheet.title = "business_units"
sheet.append(["business_unit_id", "business_unit_name"])
sheet.append(["BR-001", "Head Office"])
book.save(sys.argv[1])
`,
      file,
    ]);
    const mapping = (await post(bank, "/mapping-configs", {
      source_system: "EXCEL_CSV",
      name: "Story institution hierarchy",
      activate: true,
      reason: "Declare the synthetic bank's branch register",
      config: {
        field_mappings: {},
        product_mappings: {},
        reference_mappings: {
          business_units: {
            source_table: "business_units",
            dataset_kind: "business_units",
          },
        },
      },
    })) as { id: string };
    const form = new FormData();
    form.set(
      "file",
      new Blob([new Uint8Array(readFileSync(file))], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }),
      "business-units.xlsx",
    );
    const upload = (await post(bank, "/ingestion-uploads", form)) as {
      location: string;
    };
    const accepted = (await post(bank, "/ingestion-batches", {
      source_system: "EXCEL_CSV",
      mapping_config_id: mapping.id,
      as_of_date: new Date().toISOString().slice(0, 10),
      location: upload.location,
      reason: "Accept the synthetic branch register through Data Engine",
    })) as { batch: { status: string; records_accepted: number } };
    expect(accepted.batch).toMatchObject({
      status: "accepted",
      records_accepted: 1,
    });
  } finally {
    rmSync(directory, { recursive: true });
  }
}

async function rejectPendingRequests(
  token: string,
  userIds: readonly string[],
) {
  const headers = { Authorization: `Bearer ${token}` };
  const base = `${E2E_API_ORIGIN}/api/v1/authorization/access-requests`;
  const response = await fetch(base, { headers });
  expect(response.status).toBe(200);
  const pending = AccessRequestListReadFromJSON(
    await response.json(),
  ).requests.filter(
    (request) =>
      request.status === "pending" && userIds.includes(request.requesterUserId),
  );
  for (const request of pending) {
    const rejected = await fetch(`${base}/${request.id}/reject`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        reason_category: "other",
        reason_detail: "Retire the synthetic story's pending request",
      }),
    });
    expect(rejected.status, await rejected.text()).toBe(200);
  }
}

export const test = base.extend<{
  storyConfig: Configuration;
  storyIdentity: Story;
}>({
  storyConfig: [{ roles: [] }, { option: true }],
  storyIdentity: [
    async ({ browser, storyConfig }, provide) => {
      if (storyConfig.roles.length === 0)
        throw new Error("Declare this story's tested identity profiles");
      const suffix = randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase();
      const organizationId = storyConfig.ownedTenant
        ? `OR-${suffix}`
        : E2E_ORG_ID;
      const bankId = storyConfig.ownedTenant ? `BK-${suffix}` : "BK-SAMP0001";
      const request = {
        organization_id: organizationId,
        bank_id: bankId,
        owned_tenant: storyConfig.ownedTenant ?? false,
        signing: storyConfig.signing ?? false,
        owner_read_modules: storyConfig.ownerReadModules,
        institution_type: storyConfig.institutionType,
        bindings: (storyConfig.bindings ?? []).map((binding) => ({
          actor_role: binding.role ?? "viewer",
          role_bundle: binding.bundle,
          module_scope: binding.module,
          sensitivity_scope: binding.sensitivity,
          data_scope_kind: binding.branches ? "branch" : "all",
          data_scope_values: binding.branches ?? [],
        })),
        actors: storyConfig.roles.map((role) => ({
          role,
          user_id: randomUUID(),
        })),
      };
      const identities = new Map<string, Identity>();
      const states = new Map<string, string | undefined>();
      const contexts = new Set(browser.contexts());
      const tokens: string[] = [];
      // Provisioning administration is separate from the story's tested actors.
      let operatorToken = await mintBackendToken("admin");
      for (const role of storyConfig.roles) {
        identities.set(role, {
          ...E2E_USERS[role],
          roles: [...E2E_USERS[role].roles],
        });
        const file = path.join(E2E_TMP, `${role}.json`);
        states.set(
          file,
          existsSync(file) ? readFileSync(file, "utf8") : undefined,
        );
      }
      try {
        const { actors } = JSON.parse(await fixture("provision", request)) as {
          actors: Actor[];
        };
        for (const actor of actors) {
          Object.assign(E2E_USERS[actor.role], {
            id: actor.user_id,
            organizationId,
            roles: [actor.scalar_role],
            authv: actor.authorization_version,
            email: actor.email,
            displayName: actor.display_name,
          });
          await writeStorageState(actor.role, E2E_BASE_URL, E2E_TMP);
          const token = await mintBackendToken(actor.role);
          tokens.push(token);
          const me = await fetch(`${E2E_API_ORIGIN}/api/v1/auth/me`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          expect(me.status).toBe(200);
          expect(await me.json()).toMatchObject({
            user_id: actor.user_id,
            organization_id: organizationId,
          });
        }
        if (storyConfig.ownedTenant)
          operatorToken = await mintBackendToken("admin");
        if (storyConfig.branchRegister) await prepareBranches(bankId);
        await provide({ organizationId, bankId, actors });
      } finally {
        try {
          const closed = await Promise.allSettled(
            browser
              .contexts()
              .filter((context) => !contexts.has(context))
              .map((context) => context.close()),
          );
          try {
            try {
              await rejectPendingRequests(
                operatorToken,
                request.actors.map((actor) => actor.user_id),
              );
            } finally {
              await fixture("retire", request);
            }
            for (const token of tokens) {
              const me = await fetch(`${E2E_API_ORIGIN}/api/v1/auth/me`, {
                headers: { Authorization: `Bearer ${token}` },
              });
              expect(me.status).toBe(401);
            }
          } finally {
            const failure = closed.find(
              (result) => result.status === "rejected",
            );
            if (failure?.status === "rejected") throw failure.reason;
          }
        } finally {
          for (const [role, original] of identities) {
            const current = E2E_USERS[role];
            for (const key of [
              "organizationId",
              "email",
              "displayName",
            ] as const) {
              if (!(key in original)) delete current[key];
            }
            Object.assign(current, original);
          }
          for (const [file, original] of states) {
            if (original === undefined) rmSync(file, { force: true });
            else writeFileSync(file, original);
          }
        }
      }
    },
    { auto: true },
  ],
  context: async ({ storyIdentity, context }, provide) => {
    const cookie = (await context.cookies()).find(
      (item) => item.name === "authjs.session-token",
    );
    if (cookie) {
      const session = await decode({
        token: cookie.value,
        secret: E2E_AUTH_SECRET,
        salt: cookie.name,
      });
      expect(storyIdentity.actors.map((actor) => actor.user_id)).toContain(
        session?.sub,
      );
    }
    await provide(context);
  },
});
