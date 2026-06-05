import type { DocumentRead } from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { DocumentsTab } from "./documents-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

describe("DocumentsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders upload request controls and empty document state", async () => {
    vi.spyOn(riskApi, "documents").mockResolvedValue([]);

    renderWithQuery(<DocumentsTab tenant={tenant} caseId="case-1" />);

    expect(await screen.findByText("Request upload")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create request" })).toBeInTheDocument();
    expect(await screen.findByText("No documents uploaded")).toBeInTheDocument();
  });

  it("submits upload requests with normalized form values", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "documents").mockResolvedValue([]);
    const requestUpload = vi.spyOn(riskApi, "requestUpload").mockResolvedValue({
      documentId: "document-1",
      uploadUrl: "https://storage.example/upload",
      method: "PUT",
      headers: { "Content-Type": "application/pdf" },
      expiresInSeconds: 900,
    });

    renderWithQuery(<DocumentsTab tenant={tenant} caseId="case-1" />);

    await user.clear(await screen.findByPlaceholderText("Filename"));
    await user.type(screen.getByPlaceholderText("Filename"), "statement.pdf");
    await user.clear(screen.getByPlaceholderText("Byte size"));
    await user.type(screen.getByPlaceholderText("Byte size"), "2048");
    await user.click(screen.getByRole("button", { name: "Create request" }));

    await waitFor(() => {
      expect(requestUpload).toHaveBeenCalledWith(tenant, {
        caseId: "case-1",
        filename: "statement.pdf",
        contentType: "application/pdf",
        byteSize: 2048,
        sha256: null,
      });
    });
    expect(await screen.findByText("Upload request")).toBeInTheDocument();
  });

  it("renders document lifecycle actions for uploaded documents", async () => {
    const document: DocumentRead = {
      id: "document-1",
      organizationId: DEFAULT_ORG_ID,
      caseId: "case-1",
      storedObjectId: "stored-object-1",
      filename: "statement.pdf",
      documentType: null,
      source: "upload",
      status: "uploaded",
      parseStatus: "not_started",
      parseError: null,
      uploadedBy: DEFAULT_USER_ID,
      uploadedAt: new Date(),
      createdAt: new Date(),
      updatedAt: new Date(),
      deletedAt: null,
    };
    vi.spyOn(riskApi, "documents").mockResolvedValue([document]);

    renderWithQuery(<DocumentsTab tenant={tenant} caseId="case-1" />);

    expect(await screen.findByText("statement.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Parse" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Download URL" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Complete" })).toBeDisabled();
  });
});
