import assert from "node:assert/strict";
import NodeModule from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";
import * as modules from "../../lib/modules";

const institutions = [
  { id: "BK-AAAAAAAA", name: "Alpha" },
  { id: "BK-BBBBBBBB", name: "Beta" },
  { id: "BK-CCCCCCCC", name: "Gamma" },
];
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
    return { useBankContext: () => ({ bank: institutions[1] }) };
  }
  if (request === "@/components/profile/ProfileProvider") {
    return {
      useUserProfile: () => ({
        effectiveAuthority: {
          institutionCapabilities: [
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
    return { useBanks: () => ({ data: { banks: institutions } }) };
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
} finally {
  loader._load = originalLoad;
}
console.log(
  "AccessDeniedPage: target and missing permissions follow institution selection",
);
