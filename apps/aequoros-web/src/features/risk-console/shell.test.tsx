import { CaseSort, RiskLevel } from "@aequoros/risk-service-api";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID, DEFAULT_TENANT_OPTIONS } from "../../lib/constants";
import { mockCaseList } from "../demo-data/demo-data";
import { Sidebar, TopBar } from "./shell";

type SidebarProps = Parameters<typeof Sidebar>[0];
type TopBarProps = Parameters<typeof TopBar>[0];

describe("risk console shell", () => {
  const tenants = DEFAULT_TENANT_OPTIONS;
  it("routes sidebar tab clicks through the provided handler", () => {
    const onTab = vi.fn<SidebarProps["onTab"]>();

    render(<Sidebar activeTab="overview" onTab={onTab} />);

    fireEvent.click(screen.getByRole("button", { name: "Reports" }));

    expect(onTab).toHaveBeenCalledWith("report");
  });

  it("wires named organization, case, queue, and demo controls in the top bar", async () => {
    const user = userEvent.setup();
    const chooseTenant = vi.fn<TopBarProps["chooseTenant"]>();
    const toggleQueue = vi.fn<TopBarProps["toggleQueue"]>();
    const seed = vi.fn<TopBarProps["seed"]>();
    const cases = mockCaseList(
      DEFAULT_ORG_ID,
      {
        q: "",
        status: "all",
        risk: RiskLevel.High,
        archived: false,
        sort: CaseSort.UpdatedAtDesc,
      },
      1,
    ).items;

    render(
      <TopBar
        orgId={DEFAULT_ORG_ID}
        tenants={tenants}
        chooseTenant={chooseTenant}
        cases={cases}
        caseId={cases[0].id}
        chooseCase={vi.fn<TopBarProps["chooseCase"]>()}
        queueVisible={false}
        toggleQueue={toggleQueue}
        refresh={vi.fn<TopBarProps["refresh"]>()}
        seed={seed}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Show case queue" }));
    await user.click(screen.getByRole("button", { name: /Demo seed data/ }));

    expect(
      screen.getByRole("combobox", { name: "Current case" }),
    ).toHaveTextContent(cases[0].title);
    expect(
      screen.getByRole("combobox", { name: "Organization" }),
    ).toHaveTextContent("AequorOS Demo Organization");
    expect(screen.queryByLabelText("Tenant org id")).toBeNull();
    expect(screen.queryByLabelText("User id")).toBeNull();
    expect(chooseTenant).not.toHaveBeenCalled();
    expect(toggleQueue).toHaveBeenCalledOnce();
    expect(seed).toHaveBeenCalledOnce();
  });

  it("renders tenant options supplied by the configured data source", () => {
    const configuredTenants = [
      {
        name: "Configured Bank",
        orgId: "33333333-3333-4333-8333-333333333333",
        userId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      },
    ];

    render(
      <TopBar
        orgId={configuredTenants[0].orgId}
        tenants={configuredTenants}
        chooseTenant={vi.fn<TopBarProps["chooseTenant"]>()}
        cases={[]}
        chooseCase={vi.fn<TopBarProps["chooseCase"]>()}
        queueVisible={false}
        toggleQueue={vi.fn<TopBarProps["toggleQueue"]>()}
        refresh={vi.fn<TopBarProps["refresh"]>()}
        seed={vi.fn<TopBarProps["seed"]>()}
      />,
    );

    expect(
      screen.getByRole("combobox", { name: "Organization" }),
    ).toHaveTextContent("Configured Bank");
    expect(screen.queryByText("AequorOS Demo Organization")).toBeNull();
  });
});
