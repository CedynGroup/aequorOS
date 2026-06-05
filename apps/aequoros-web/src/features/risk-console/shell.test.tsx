import { CaseSort, RiskLevel } from "@aequoros/risk-service-api";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { mockCaseList } from "../demo-data/demo-data";
import { Sidebar, TopBar } from "./shell";

type SidebarProps = Parameters<typeof Sidebar>[0];
type TopBarProps = Parameters<typeof TopBar>[0];

describe("risk console shell", () => {
  it("routes sidebar tab clicks through the provided handler", () => {
    const onTab = vi.fn<SidebarProps["onTab"]>();

    render(<Sidebar activeTab="overview" onTab={onTab} />);

    fireEvent.click(screen.getByRole("button", { name: "Reports" }));

    expect(onTab).toHaveBeenCalledWith("report");
  });

  it("wires tenant, user, and demo seed controls in the top bar", async () => {
    const user = userEvent.setup();
    const setOrgId = vi.fn<TopBarProps["setOrgId"]>();
    const setUserId = vi.fn<TopBarProps["setUserId"]>();
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
        userId={DEFAULT_USER_ID}
        setOrgId={setOrgId}
        setUserId={setUserId}
        cases={cases}
        caseId={cases[0].id}
        chooseCase={vi.fn<TopBarProps["chooseCase"]>()}
        refresh={vi.fn<TopBarProps["refresh"]>()}
        seed={seed}
      />,
    );

    fireEvent.change(screen.getByLabelText("Tenant org id"), {
      target: { value: "org-2" },
    });
    fireEvent.change(screen.getByLabelText("User id"), {
      target: { value: "user-2" },
    });
    await user.click(screen.getByRole("button", { name: /Demo seed data/ }));

    expect(screen.getByRole("combobox", { name: "Current case" })).toHaveTextContent(
      "Covenant review - Northstar Foods",
    );
    expect(setOrgId).toHaveBeenLastCalledWith("org-2");
    expect(setUserId).toHaveBeenLastCalledWith("user-2");
    expect(seed).toHaveBeenCalledOnce();
  });
});
