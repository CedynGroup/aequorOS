import { CaseSort } from "@aequoros/risk-service-api";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { mockCaseList } from "../demo-data/demo-data";
import { CaseQueuePanel } from "./case-queue-panel";

type QueuePanelProps = Parameters<typeof CaseQueuePanel>[0];

const tenant = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

const filters = {
  q: "",
  status: "all" as const,
  risk: "all" as const,
  archived: false,
  sort: CaseSort.UpdatedAtDesc,
};

function renderQueue(overrides: Partial<QueuePanelProps> = {}) {
  const data = mockCaseList(DEFAULT_ORG_ID, filters, 1);
  const props: QueuePanelProps = {
    query: {
      data,
      error: null,
      isError: false,
      isLoading: false,
    },
    taxonomy: {
      statuses: ["in_review", "active"],
      riskLevels: ["high", "medium"],
      sortOptions: [CaseSort.UpdatedAtDesc, CaseSort.RiskScoreDesc],
    },
    filters,
    page: 1,
    selected: {},
    setSelected: vi.fn<QueuePanelProps["setSelected"]>(),
    chooseCase: vi.fn<QueuePanelProps["chooseCase"]>(),
    activeCaseId: data.items[0].id,
    selectedIds: [],
    tenant,
    updateSearch: vi.fn<QueuePanelProps["updateSearch"]>(),
    ...overrides,
  };

  return {
    data,
    props,
    ...renderWithQuery(<CaseQueuePanel {...props} />),
  };
}

describe("CaseQueuePanel", () => {
  it("renders queue rows with dense operational columns", () => {
    renderQueue();

    expect(screen.getByText("Case Queue")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select cases" })).toBeDisabled();
    expect(screen.getByText("Covenant review - Northstar Foods")).toBeInTheDocument();
    expect(screen.getByText("In Review")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("Needs More Info")).toBeInTheDocument();
    expect(screen.getByText("82")).toHaveAttribute(
      "title",
      expect.stringMatching(/^Demo score \d{4}-\d{2}-\d{2} run 1$/),
    );
  });

  it("sends search filter changes back to route search state", () => {
    const updateSearch = vi.fn<QueuePanelProps["updateSearch"]>();

    renderQueue({ updateSearch });
    fireEvent.change(screen.getByPlaceholderText("Search cases"), {
      target: { value: "Northstar" },
    });

    expect(updateSearch).toHaveBeenCalledWith({ q: "Northstar", page: 1 });
  });

  it("selects all visible case ids from the header checkbox", async () => {
    const user = userEvent.setup();
    const setSelected = vi.fn<QueuePanelProps["setSelected"]>();
    const { data } = renderQueue({ setSelected });

    await user.click(screen.getByLabelText("Select all cases"));

    expect(setSelected).toHaveBeenCalledWith(
      Object.fromEntries(data.items.map((item) => [item.id, true])),
    );
  });

  it("opens selected cases through the provided case handler", async () => {
    const user = userEvent.setup();
    const chooseCase = vi.fn<QueuePanelProps["chooseCase"]>();
    const { data } = renderQueue({ chooseCase });

    await user.click(screen.getByRole("button", { name: data.items[0].title }));

    expect(chooseCase).toHaveBeenCalledWith(data.items[0].id);
  });

  it("shows selected bulk action counts", () => {
    const data = mockCaseList(DEFAULT_ORG_ID, filters, 1);

    renderQueue({ selectedIds: [data.items[0].id] });

    expect(screen.getByRole("button", { name: "Bulk actions (1)" })).toBeEnabled();
  });

  it("renders empty and API error states distinctly", () => {
    const { props, rerender } = renderQueue({
      query: {
        data: { items: [], total: 0, pages: 0, hasMore: false },
        error: null,
        isError: false,
        isLoading: false,
      },
    });

    expect(screen.getByText("No cases match the current filters.")).toBeInTheDocument();

    rerender(
      <CaseQueuePanel
        {...props}
        query={{
          data: undefined,
          error: { statusCode: 404, code: "not_found", message: "No cases" },
          isError: true,
          isLoading: false,
        }}
      />,
    );

    expect(screen.getByText("404 not_found")).toBeInTheDocument();
    expect(screen.getByText("No cases")).toBeInTheDocument();
  });
});
