"use client";

/**
 * Download the cycle as a working document.
 *
 * Both artifacts are DRAFTS — the document says so on every page, and the
 * filenames end in `-DRAFT`. Neither is a filing, neither is signed, and the
 * Word file is explicitly a working copy. The filing package is P3 work.
 */

import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import { downloadIcaapDraft } from "@/lib/api/icaap";
import { useModuleScope } from "@/components/shell/BankContext";
import { SecondaryButton } from "./Dialog";

export default function DraftExportMenu({
  bankId,
  cycleId,
  content = "working",
}: {
  bankId: string;
  cycleId: string;
  content?: "working" | "committed";
}) {
  const scope = useModuleScope();
  const [busy, setBusy] = useState<"pdf" | "docx" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canExport = scope.capitalExport === true;

  const download = async (kind: "pdf" | "docx") => {
    setBusy(kind);
    setError(null);
    try {
      await downloadIcaapDraft(bankId, cycleId, kind, content);
    } catch (caught) {
      setError(
        isApiError(caught)
          ? caught.message
          : "Could not download the draft. Try again.",
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <SecondaryButton
          onClick={() => void download("pdf")}
          disabled={!canExport || busy !== null}
          title={
            canExport
              ? undefined
              : "Requires Basel Capital · Confidential · Export."
          }
        >
          <FileText size={13} aria-hidden />
          {busy === "pdf" ? "Preparing…" : "Draft PDF"}
        </SecondaryButton>
        <SecondaryButton
          onClick={() => void download("docx")}
          disabled={!canExport || busy !== null}
          title={
            canExport
              ? undefined
              : "Requires Basel Capital · Confidential · Export."
          }
        >
          <Download size={13} aria-hidden />
          {busy === "docx" ? "Preparing…" : "Draft Word (working copy)"}
        </SecondaryButton>
      </div>
      {error && <p className="text-caption text-critical">{error}</p>}
    </div>
  );
}
