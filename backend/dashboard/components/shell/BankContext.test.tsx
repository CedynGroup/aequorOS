import assert from "node:assert/strict";
import NodeModule from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";
import * as modules from "../../lib/modules";

let pathname = "/settings";
const bank = {
  id: "BK-AAAAAAAA",
  name: "Bank",
  currency: "GHS",
  jurisdictionCode: "GH",
};
const loadRequests: boolean[] = [];
const loader = NodeModule as typeof NodeModule & {
  _load: (request: string, parent: unknown, isMain: boolean) => unknown;
};
const originalLoad = loader._load;
loader._load = (request, parent, isMain) => {
  if (request === "next/navigation") return { usePathname: () => pathname };
  if (request === "@/lib/modules") return modules;
  if (request === "@/lib/api/hooks")
    return {
      useBanks: (enabled: boolean) => {
        loadRequests.push(enabled);
        return {
          data: enabled ? { banks: [bank] } : undefined,
          isLoading: false,
        };
      },
      useReportingPeriods: () => ({ data: { periods: [] }, isLoading: false }),
    };
  if (request === "@/components/profile/ProfileProvider")
    return {
      useUserProfile: () => ({
        isLoading: false,
        effectiveAuthority: {
          institutionCapabilities: [],
          organizationCapabilities: [],
        },
      }),
    };
  if (request === "@/lib/api/client") return { isApiError: () => false };
  if (request === "@/lib/loginUrl") return {};
  if (request === "@/lib/format")
    return { setActiveJurisdiction: () => undefined };
  if (request === "./Logo") return { __esModule: true, default: () => null };
  return originalLoad(request, parent, isMain);
};
try {
  const { default: BankProvider, useBankContext } = require("./BankContext");
  function Consumer() {
    const { bank: selected } = useBankContext();
    return <span>{selected?.id ?? "no bank"}</span>;
  }
  for (const [path, expected] of [
    ["/settings", true],
    ["/settings/profile", false],
    ["/settings/profile/preferences", false],
  ] as const) {
    pathname = path;
    let renderer: ReturnType<typeof create>;
    act(() => {
      renderer = create(
        <BankProvider>
          <Consumer />
        </BankProvider>,
      );
    });
    assert.equal(loadRequests.at(-1), expected, path);
    assert.equal(
      renderer!.root.findByType("span").children.join(""),
      expected ? bank.id : "no bank",
    );
    act(() => renderer.unmount());
  }
} finally {
  loader._load = originalLoad;
}
console.log(
  "BankContext: Settings loads institutions; profile remains self-service",
);
