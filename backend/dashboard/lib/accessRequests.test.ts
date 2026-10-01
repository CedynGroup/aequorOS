import assert from "node:assert/strict";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { execFileSync } from "node:child_process";
import {
  accessDeniedForPath,
  accessRequestRequirements,
  effectiveInstitutionModules,
  isPathVisible,
  isHrefVisible,
  PUBLIC_MODULE_ROUTES,
  type ModuleScope,
} from "./modules";

import { reasonDraftComplete } from "../components/access/GrantReasonFields";

for (const reasonCategory of [
  "temporary_cover",
  "incident_break_glass",
] as const) {
  const draft = {
    reasonCategory,
    reasonDetail: "",
    reference: "",
    validUntil: "",
  };
  assert.equal(reasonDraftComplete(draft), false);
  assert.equal(reasonDraftComplete(draft, { expiry: false }), true);
  assert.equal(
    reasonDraftComplete({ ...draft, validUntil: "2099-01-01T12:00" }),
    true,
  );
}
assert.equal(
  reasonDraftComplete(
    {
      reasonCategory: "other",
      reasonDetail: "",
      reference: "",
      validUntil: "",
    },
    { expiry: false },
  ),
  false,
);

const serverRequirements = JSON.parse(
  execFileSync(
    "uv",
    [
      "run",
      "--frozen",
      "--project",
      "..",
      "python",
      "-c",
      `import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
from app.features.manage_authorization import _PUBLIC_ACCESS_REQUEST_ROUTES, _route_requirement, _access_request_binding
binding_id = uuid4()
request = SimpleNamespace(organization_id="OR-DEM00001", requester_user_id=uuid4(), institution_id="BK-SAMP0001", module_scope="credit", permission="view")
db = Mock()
with patch("app.features.manage_authorization.authorization.evaluate_permission", return_value=SimpleNamespace(allowed=True, matching_binding_ids=[binding_id])), patch("app.features.manage_authorization.authorization.effective_data_scope", return_value=SimpleNamespace(whole_institution=False)):
    for route, sensitivity, allowed in [
        ("/credit/book", "restricted", True),
        ("/credit/activity", "confidential", True),
        ("/credit/activity", "aggregated", False),
        ("/credit/concentration", "restricted", False),
        ("/credit/concentration", "aggregated", False),
    ]:
        request.route, request.sensitivity_scope = route, sensitivity
        assert (_access_request_binding(db, request) is not None) == allowed, (route, sensitivity)
print(json.dumps({route: [[part.value for part in requirement] for requirement in _route_requirement(route)[1]] for route in _PUBLIC_ACCESS_REQUEST_ROUTES}))`,
    ],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        PYTHONPATH: "..",
        APP_ENV: "test",
        DATABASE_URL: "",
      },
      encoding: "utf8",
    },
  ),
) as Record<string, string[][]>;
assert.deepEqual(
  [...PUBLIC_MODULE_ROUTES].sort(),
  Object.keys(serverRequirements).sort(),
);
for (const route of PUBLIC_MODULE_ROUTES) {
  const clientRequirements = new Set(
    ["bank", "sdi"].flatMap((institutionClass) =>
      accessRequestRequirements(route, [], institutionClass).map((required) =>
        [
          required.moduleScope,
          required.sensitivityScope,
          required.permission,
        ].join("/"),
      ),
    ),
  );
  assert.deepEqual(
    [...clientRequirements].sort(),
    serverRequirements[route].map((required) => required.join("/")).sort(),
    `${route}: every advertised request must be accepted by the server`,
  );
}

function capability(
  module: EffectiveCapabilityRead["module"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
  kind: EffectiveCapabilityRead["dataScope"]["kind"],
): EffectiveCapabilityRead {
  return {
    module,
    sensitivity,
    permission: "view",
    requiresContextualAuthorization: false,
    dataScope: { kind, branches: [], regions: [] },
  };
}

for (const [route, sensitivities] of [
  ["/credit/book", ["restricted"]],
  ["/credit/concentration", ["aggregated", "restricted"]],
  ["/credit/activity", ["aggregated", "confidential"]],
  ["/credit/delinquency", ["aggregated"]],
  ["/credit/vintages", ["aggregated"]],
] as const) {
  assert.deepEqual(
    accessRequestRequirements(route, [], "bank").map(
      (required) => required.sensitivityScope,
    ),
    sensitivities,
    route,
  );
}
for (const kind of ["branch", "region", "mixed", "none", "all"] as const) {
  assert.equal(
    accessRequestRequirements(
      "/basel",
      [capability("cap", "aggregated", kind)],
      "bank",
    ).length,
    kind === "all" ? 0 : 1,
  );
}
assert.deepEqual(
  accessRequestRequirements(
    "/credit/activity",
    [
      capability("credit", "aggregated", "branch"),
      capability("credit", "confidential", "branch"),
    ],
    "bank",
  ).map((required) => required.sensitivityScope),
  ["aggregated"],
);
assert.equal(
  accessRequestRequirements(
    "/credit/book",
    [capability("credit", "restricted", "branch")],
    "bank",
  ).length,
  0,
);
assert.equal(
  accessRequestRequirements(
    "/credit/concentration",
    [
      capability("credit", "aggregated", "all"),
      capability("credit", "restricted", "branch"),
    ],
    "bank",
  ).length,
  1,
);

for (const [capabilities, route, missing] of [
  [[capability("credit", "aggregated", "all")], "/credit/book", ["restricted"]],
  [[capability("credit", "restricted", "branch")], "/credit/book", []],
  [
    [capability("credit", "aggregated", "all")],
    "/credit/concentration",
    ["restricted"],
  ],
  [
    [capability("credit", "restricted", "all")],
    "/credit/concentration",
    ["aggregated"],
  ],
  [
    [
      capability("credit", "aggregated", "all"),
      capability("credit", "restricted", "branch"),
    ],
    "/credit/concentration",
    ["restricted"],
  ],
  [
    [
      capability("credit", "aggregated", "all"),
      capability("credit", "restricted", "all"),
    ],
    "/credit/concentration",
    [],
  ],
  [
    [capability("credit", "aggregated", "all")],
    "/credit/activity",
    ["confidential"],
  ],
  [
    [
      capability("credit", "aggregated", "branch"),
      capability("credit", "confidential", "branch"),
    ],
    "/credit/activity",
    ["aggregated"],
  ],
  [
    [
      capability("credit", "aggregated", "all"),
      capability("credit", "confidential", "branch"),
    ],
    "/credit/activity",
    [],
  ],
  [[capability("credit", "aggregated", "branch")], "/credit", ["aggregated"]],
  [[capability("credit", "aggregated", "all")], "/credit/vintages", []],
  [
    [capability("credit", "aggregated", "region")],
    "/credit/delinquency",
    ["aggregated"],
  ],
] as const) {
  const scope: ModuleScope = {
    modules: effectiveInstitutionModules(null, capabilities),
    institutionCapabilities: capabilities,
    organizationModules: new Set(),
    hasInstitutionAuthority: true,
    institutionClass: "bank",
    isResolved: true,
  };
  assert.equal(isPathVisible(route, scope), missing.length === 0, route);
  assert.equal(isHrefVisible(route, scope), missing.length === 0, route);
  assert.deepEqual(
    accessDeniedForPath(route, scope)?.requirements.map(
      (item) => item.sensitivityScope,
    ) ?? [],
    missing,
    route,
  );
  assert.deepEqual(
    accessRequestRequirements(route, capabilities, "bank").map(
      (item) => item.sensitivityScope,
    ),
    missing,
    route,
  );
}

console.log("accessRequests: dashboard and server route requirements agree");
