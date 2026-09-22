import assert from "node:assert/strict";
import NodeModule from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";
import * as modules from "../../lib/modules";

const institutions = [
  { id: "BK-AAAAAAAA", name: "Alpha", institutionClass: "bank" },
  { id: "BK-BBBBBBBB", name: "Beta", institutionClass: "bank" },
  { id: "BK-CCCCCCCC", name: "Gamma", institutionClass: "bank" },
];
let baselineOnly = false;
let activeBank: (typeof institutions)[number] | null = institutions[1];
const capability = (module: string) => ({
  module,
  sensitivity: "confidential",
  permission: "view",
  requiresContextualAuthorization: false,
});
const submitted: Record<string, unknown>[] = [];
const loader = NodeModule as typeof NodeModule & {
  _load: (request: string, parent: unknown, isMain: boolean) => unknown;
};
const originalLoad = loader._load;
const container = ({ children }: { children?: React.ReactNode }) => (
  <div>{children}</div>
);
loader._load = (request, parent, isMain) => {
  if (request === "@/lib/modules") return modules;
  if (request === "@/components/shell/BankContext") {
    return { useBankContext: () => ({ bank: activeBank }) };
  }
  if (request === "@/components/profile/ProfileProvider") {
    return {
      useUserProfile: () => ({
        effectiveAuthority: {
          institutionCapabilities: baselineOnly
            ? []
            : [
                {
                  institutionId: institutions[0].id,
                  capabilities: [capability("risk")],
                },
                {
                  institutionId: institutions[1].id,
                  capabilities: [capability("liq")],
                },
                {
                  institutionId: institutions[2].id,
                  capabilities: [capability("liq"), capability("risk")],
                },
              ],
        },
      }),
    };
  }
  if (request === "@/lib/api/hooks") {
    return { useBanks: () => ({ data: { banks: [] } }) };
  }
  if (request === "@tanstack/react-query")
    return {
      useQueryClient: () => ({ invalidateQueries: () => undefined }),
      useQuery: ({ queryKey }: { queryKey: string[] }) => ({
        data:
          queryKey[1] === "request-institutions"
            ? { institutions }
            : { requests: [] },
      }),
      useMutation: ({ mutationFn }: { mutationFn: () => unknown }) => ({
        mutate: mutationFn,
      }),
    };
  if (request === "@/lib/api/client")
    return {
      authorizationApi: {
        createAuthorizationAccessRequest: ({
          accessRequestCreate,
        }: {
          accessRequestCreate: Record<string, unknown>;
        }) => {
          submitted.push(accessRequestCreate);
          return Promise.resolve({});
        },
      },
    };
  if (request === "@/components/ui/PageHeader")
    return { default: container, __esModule: true };
  if (request === "@/components/ui/Card")
    return { Card: container, CardBody: container };
  if (request === "@/components/access/GrantReasonFields")
    return {
      GrantReasonFields: container,
      reasonDraftComplete: () => true,
    };
  if (request === "@/lib/format") return { fmtLocale: () => "en" };
  return originalLoad(request, parent, isMain);
};

try {
  const AccessDeniedPage = require("./AccessDeniedPage").default;
  const requirements = modules.accessRequestRequirements(
    "/liquidity/stress",
    [],
    "bank",
  );
  let renderer: ReturnType<typeof create>;
  act(() => {
    renderer = create(
      <AccessDeniedPage
        route="/liquidity/stress"
        denied={{
          title: "Liquidity stress",
          reason: "Access required",
          requirements: requirements.slice(1),
        }}
      />,
    );
  });
  act(() => renderer.root.findByType("button").props.onClick());
  assert.equal(
    renderer!.root.findByType("select").props.value,
    institutions[1].id,
  );
  act(() =>
    renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
  );
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].institutionId, institutions[1].id);
  assert.equal(submitted[0].moduleScope, "risk");
  act(() =>
    renderer.root
      .findByType("select")
      .props.onChange({ target: { value: institutions[0].id } }),
  );
  act(() =>
    renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
  );
  assert.equal(submitted.length, 2);
  assert.equal(submitted[1].institutionId, institutions[0].id);
  assert.equal(submitted[1].moduleScope, "liq");
  act(() =>
    renderer.root
      .findByType("select")
      .props.onChange({ target: { value: institutions[2].id } }),
  );
  assert.equal(renderer!.root.findAllByType("button").length, 0);
  assert.match(
    renderer!.root.findByProps({ role: "status" }).children.join(""),
    /No additional permissions/,
  );
  act(() =>
    renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
  );
  assert.equal(submitted.length, 2);
  act(() => renderer.unmount());
  activeBank = null;
  baselineOnly = true;
  institutions.splice(0, institutions.length, {
    id: "BK-SDI00001",
    name: "Savings and loans",
    institutionClass: "sdi",
  });
  act(() => {
    renderer = create(
      <AccessDeniedPage
        route="/liquidity"
        denied={{
          title: "Liquidity",
          reason: "Access required",
          requirements: modules.accessRequestRequirements(
            "/liquidity",
            [],
            null,
          ),
        }}
      />,
    );
  });
  act(() => renderer.root.findByType("button").props.onClick());
  act(() =>
    renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
  );
  assert.equal(submitted.length, 3);
  assert.equal(submitted[2].institutionId, "BK-SDI00001");
  assert.equal(submitted[2].moduleScope, "liq");
  assert.equal(submitted[2].sensitivityScope, "confidential");
  act(() => renderer.unmount());
  institutions.splice(
    0,
    institutions.length,
    { id: "BK-SDI00001", name: "Alpha", institutionClass: "sdi" },
    { id: "BK-BANK0001", name: "Beta", institutionClass: "bank" },
  );
  act(() => {
    renderer = create(
      <AccessDeniedPage
        route="/basel/planning"
        denied={{
          title: "Capital planning",
          reason: "Access required",
          requirements: modules.accessRequestRequirements(
            "/basel/planning",
            [],
            null,
          ),
        }}
      />,
    );
  });
  assert.equal(renderer!.root.findAllByType("form").length, 0);
  assert.equal(renderer!.root.findByType("select").props.value, "BK-SDI00001");
  assert.equal(renderer!.root.findByType("button").props.disabled, true);
  act(() =>
    renderer.root.findByType("select").props.onChange({
      target: { value: "BK-BANK0001" },
    }),
  );
  assert.equal(renderer!.root.findByType("button").props.disabled, false);
  act(() => renderer.root.findByType("button").props.onClick());
  act(() =>
    renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
  );
  assert.equal(submitted.length, 4);
  assert.equal(submitted[3].institutionId, "BK-BANK0001");
  assert.equal(submitted[3].moduleScope, "cap");
  assert.equal(submitted[3].sensitivityScope, "confidential");
  assert.equal(submitted[3].permission, "view");
  act(() => renderer.unmount());
} finally {
  loader._load = originalLoad;
}
console.log(
  "AccessDeniedPage: target and missing permissions follow institution selection",
);
