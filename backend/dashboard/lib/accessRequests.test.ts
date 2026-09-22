import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { accessRequestRequirements, PUBLIC_MODULE_ROUTES } from "./modules";

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
from app.features.manage_authorization import _PUBLIC_ACCESS_REQUEST_ROUTES, _route_requirement
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

console.log("accessRequests: dashboard and server route requirements agree");
