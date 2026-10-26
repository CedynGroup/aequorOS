import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { E2E_TMP } from "../../playwright.config";
import { E2E_USERS } from "./mint";

export const MACHINE_ORG = "OR-MACH0001";
export const MACHINE_BANK = "BK-MACH0001";
export const DENIED_BANK = "BK-MACH0002";
export const MACHINE_ADMIN = "machine_key_admin";
const BACKEND = path.resolve(__dirname, "../../..");
const DATABASE = path.join(E2E_TMP, "e2e.db");

/** Create identity, institutions and real authority; financial inputs arrive by API push. */
export function createMachineTenant(): void {
  const userId = randomUUID();
  const output = execFileSync(
    path.join(BACKEND, ".venv/bin/python"),
    [
      "-c",
      `
import sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.authorization import DataScope, GrantorType, InstitutionScope, ModuleScope, PrincipalType, RoleBundle, SensitivityScope
from app.db.base import utc_now
from app.identity.service import authorization, membership
from app.models import Bank, Organization, User
from app.services.parameter_register import seed_tenant_register

database, org, own, sibling, identity = sys.argv[1:6]
with Session(create_engine(f"sqlite+pysqlite:///{database}")) as session:
    session.add(Organization(id=org, name="Machine Key Journey Tenant"))
    session.flush()
    for bank_id, name in ((own, "A Machine Push Bank"), (sibling, "B Machine Denied Bank")):
        bank = Bank(id=bank_id, organization_id=org, name=name, short_name=name, currency="GHS", jurisdiction_code="GH", license_type="universal", institution_type="universal_bank")
        session.add(bank)
        session.flush()
        seed_tenant_register(session, bank=bank, approved_by="e2e-machine-key", approved_at=utc_now())
    user = User(id=UUID(identity), organization_id=org, email=f"{identity}@aequoros.example", display_name="Machine Key Administrator", role="account_admin")
    session.add(user)
    session.flush()
    membership.ensure_baseline_membership(session, user=user, granted_by_id="e2e-machine-key", commit=False)
    for role, institution, bank_id, module in (
        (RoleBundle.ACCOUNT_ADMIN, InstitutionScope.ORGANIZATION, None, ModuleScope.ACCOUNT),
        (RoleBundle.VIEWER, InstitutionScope.INSTITUTION, own, ModuleScope.DATA),
        (RoleBundle.VIEWER, InstitutionScope.INSTITUTION, sibling, ModuleScope.DATA),
    ):
        authorization.create_role_binding(
            session, organization_id=org, principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN, role_bundle=role,
            scope=authorization.BindingScope(institution, bank_id, module, SensitivityScope.RESTRICTED, data_scope=DataScope.ALL),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "e2e-machine-key"),
            reason="Exercise a bank-scoped machine credential", commit=False,
        )
    session.commit()
    session.refresh(user)
    print(user.authorization_version)
`,
      DATABASE,
      MACHINE_ORG,
      MACHINE_BANK,
      DENIED_BANK,
      userId,
    ],
    { cwd: BACKEND, encoding: "utf8" },
  );
  const authv = Number(output.trim());
  if (!Number.isSafeInteger(authv))
    throw new Error("Missing current authorization version");
  E2E_USERS[MACHINE_ADMIN] = {
    id: userId,
    organizationId: MACHINE_ORG,
    roles: ["account_admin"],
    authv,
  };
}

/** Remove only this story's tenant, including machine identity, queued work and canonical rows. */
export function removeMachineTenant(): void {
  delete E2E_USERS[MACHINE_ADMIN];
  execFileSync(
    path.join(BACKEND, ".venv/bin/python"),
    [
      "-c",
      `
import sqlite3, sys
database, org = sys.argv[1:3]
with sqlite3.connect(database) as db:
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        if "organization_id" in columns:
            db.execute(f'DELETE FROM "{table}" WHERE organization_id=?', (org,))
    db.execute("DELETE FROM organizations WHERE id=?", (org,))
    for table in ("banks", "users", "integration_keys", "jobs", "canonical_gl_accounts"):
        assert db.execute(f'SELECT COUNT(*) FROM "{table}" WHERE organization_id=?', (org,)).fetchone()[0] == 0
`,
      DATABASE,
      MACHINE_ORG,
    ],
    { cwd: BACKEND },
  );
}

/** Read persisted outcomes; no fixture writer prepares financial evidence. */
export function readMachineBank(bank: string, asOf: string) {
  const db = new DatabaseSync(DATABASE, { readOnly: true });
  try {
    db.exec("PRAGMA busy_timeout=5000");
    const accounts = db
      .prepare(
        `SELECT account_code, account_class, currency, balance,
                ingestion_batch_id, lineage_id
         FROM canonical_gl_accounts
         WHERE organization_id=? AND bank_id=? AND as_of_date=?
           AND superseded_by IS NULL AND withdrawn_at IS NULL
         ORDER BY account_code`,
      )
      .all(MACHINE_ORG, bank, asOf);
    const count = (sql: string) =>
      Number(db.prepare(sql).get(MACHINE_ORG, bank)?.n);
    return {
      accounts,
      batches: count(
        "SELECT COUNT(*) AS n FROM ingestion_batches WHERE organization_id=? AND bank_id=?",
      ),
      pushEvents: count(
        "SELECT COUNT(*) AS n FROM audit_events WHERE organization_id=? AND json_extract(details, '$.bank_id')=? AND event_type LIKE 'push_batch.%'",
      ),
      openedEvents: count(
        "SELECT COUNT(*) AS n FROM audit_events WHERE organization_id=? AND json_extract(details, '$.bank_id')=? AND event_type='push_batch.opened'",
      ),
    };
  } finally {
    db.close();
  }
}
