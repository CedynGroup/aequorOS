"use client";

/**
 * Writing one section of the ICAAP.
 *
 * Three columns: what the framework asks for, the narrative, and the figures
 * it may cite. The behaviours that matter:
 *
 *  - AUTOSAVE IS OPTIMISTIC AND NEVER OVERWRITES. Each save carries the
 *    revision the author was looking at. If someone else saved in between the
 *    server refuses (409) and the conflict dialog appears — the save is not
 *    retried, because retrying it is precisely the overwrite the check exists
 *    to prevent.
 *  - COMMITTING IS EXPLICIT. Autosaving keeps a working draft; a committed
 *    version is an immutable snapshot with an author, a note and a digest, and
 *    it is what a reviewer reads.
 *  - FIGURES ARE CITED, NOT TYPED. The editor inserts a reference to a block's
 *    fact; the value comes from the binding at render time.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Loader2, Save } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useModuleScope } from "@/components/shell/BankContext";
import {
  useCommitIcaapSection,
  useIcaapAttachments,
  useIcaapBlocks,
  useIcaapBlockTypes,
  useIcaapSection,
  useSaveIcaapSection,
  type ProseMirrorDoc,
} from "@/lib/api/icaap";
import { IcaapBlocksProvider } from "./blocksContext";
import ConflictDialog, { type SectionConflict } from "./ConflictDialog";
import DataBlockPanel from "./DataBlockPanel";
import RequirementChecklist from "./RequirementChecklist";
import SectionEditorLoader from "./SectionEditorLoader";
import VersionHistory from "./VersionHistory";
import { INPUT_CLASS, PrimaryButton } from "./Dialog";
import type { SectionEditorHandle } from "./editor/SectionEditor";

const AUTOSAVE_DELAY_MS = 1_500;

type SaveState = "idle" | "unsaved" | "saving" | "saved";

export default function SectionWorkspace({
  bankId,
  cycleId,
  sectionKey,
}: {
  bankId: string;
  cycleId: string;
  sectionKey: string;
}) {
  const scope = useModuleScope();
  const sectionQuery = useIcaapSection(bankId, cycleId, sectionKey);
  const blocksQuery = useIcaapBlocks(bankId, cycleId, { includePayload: true });
  const blockTypesQuery = useIcaapBlockTypes(bankId);
  const attachmentsQuery = useIcaapAttachments(bankId, cycleId);
  const save = useSaveIcaapSection(bankId, cycleId, sectionKey);
  const commit = useCommitIcaapSection(bankId, cycleId, sectionKey);

  const section = sectionQuery.data;
  const canEdit = scope.capitalEdit === true && section?.editable === true;

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [conflict, setConflict] = useState<SectionConflict | null>(null);
  const [commitNote, setCommitNote] = useState("");
  const [docToken, setDocToken] = useState<string>("initial");
  const editorRef = useRef<SectionEditorHandle | null>(null);
  const pendingDoc = useRef<ProseMirrorDoc | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The revision the author is working from. Server-owned; never incremented
  // locally, or the next save would claim a revision that does not exist.
  const baseRev = useRef<number>(0);

  useEffect(() => {
    if (section) baseRev.current = section.workingRev;
  }, [section]);

  /**
   * Write the pending document and adopt the revision the server returns.
   *
   * Returns false when the save did not land, so a caller that was about to do
   * something ON TOP of it (committing) stops instead of proceeding against a
   * document the server does not have.
   */
  const flush = useCallback(async (): Promise<boolean> => {
    const doc = pendingDoc.current;
    if (!doc || !canEdit) return true;
    pendingDoc.current = null;
    setSaveState("saving");
    try {
      const updated = await save.mutateAsync({ doc, baseRev: baseRev.current });
      baseRev.current = updated.workingRev;
      setSaveState("saved");
      return true;
    } catch (error) {
      setSaveState("unsaved");
      if (isApiError(error) && error.status === 409) {
        const details = (error.details ?? {}) as {
          current_rev?: number;
          updated_by?: string;
          updated_at?: string;
        };
        setConflict({
          currentRev: details.current_rev ?? null,
          updatedBy: details.updated_by ?? null,
          updatedAt: details.updated_at ? new Date(details.updated_at) : null,
          plainText: JSON.stringify(doc),
        });
      }
      return false;
    }
  }, [canEdit, save]);

  /**
   * STABLE callbacks handed to the editor.
   *
   * Tiptap captures `onUpdate` when it builds the editor, and the editor is
   * built on the first render — when the section is still loading and
   * `canEdit` is therefore false. A callback whose identity changes every
   * render left the editor holding that first, permanently-refusing copy: the
   * author typed, nothing was scheduled, nothing was saved, and the first the
   * screen said about it was a commit refused as empty. The ref indirection
   * makes the handler identity permanent and its BODY always current.
   */
  const latest = useRef({ canEdit, flush });
  latest.current = { canEdit, flush };

  const onChange = useCallback((doc: ProseMirrorDoc) => {
    if (!latest.current.canEdit) return;
    pendingDoc.current = doc;
    setSaveState("unsaved");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void latest.current.flush();
    }, AUTOSAVE_DELAY_MS);
  }, []);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onReady = useCallback((handle: SectionEditorHandle | null) => {
    editorRef.current = handle;
  }, []);

  const saveLabel: Record<SaveState, string> = {
    idle: "",
    unsaved: "Unsaved changes",
    saving: "Saving…",
    saved: "Saved",
  };

  return (
    <QueryBoundary
      isLoading={sectionQuery.isLoading}
      error={sectionQuery.error}
      onRetry={() => void sectionQuery.refetch()}
      contained
    >
      {section && (
        <IcaapBlocksProvider blocks={blocksQuery.data?.blocks ?? []}>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[20rem_minmax(0,1fr)_22rem]">
            <div className="space-y-4">
              <RequirementChecklist
                bankId={bankId}
                cycleId={cycleId}
                section={section}
                readOnly={!canEdit}
              />
              <VersionHistory
                bankId={bankId}
                cycleId={cycleId}
                sectionKey={sectionKey}
              />
            </div>

            <div className="space-y-3">
              {section.guidance && (
                <p className="text-body leading-relaxed text-slate">
                  {section.guidance}
                </p>
              )}
              <SectionEditorLoader
                initialDoc={section.workingDoc as ProseMirrorDoc}
                docToken={docToken}
                readOnly={!canEdit}
                onChange={onChange}
                onReady={onReady}
              />

              <div className="card flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                <span className="inline-flex items-center gap-1.5 text-caption text-slate">
                  {saveState === "saving" && (
                    <Loader2 size={12} className="animate-spin" aria-hidden />
                  )}
                  {saveState === "saved" && (
                    <Check size={12} className="text-success" aria-hidden />
                  )}
                  {saveLabel[saveState]}
                </span>
                <div className="flex flex-1 items-center justify-end gap-2">
                  <input
                    className={`${INPUT_CLASS} max-w-sm`}
                    value={commitNote}
                    maxLength={2000}
                    disabled={!canEdit}
                    placeholder="What changed in this version?"
                    aria-label="Commit note"
                    onChange={(event) => setCommitNote(event.target.value)}
                  />
                  <PrimaryButton
                    disabled={
                      !canEdit || commit.isPending || saveState === "saving"
                    }
                    onClick={() => {
                      if (timer.current) clearTimeout(timer.current);
                      // Commit exactly what the author is looking at: the
                      // pending edit has to REACH the server first, and its
                      // new revision is what the commit must name. Firing both
                      // at once let the commit win the race and be refused as
                      // an empty section.
                      void (async () => {
                        if (!(await flush())) return;
                        commit.mutate(
                          {
                            baseRev: baseRev.current,
                            note: commitNote.trim() ? commitNote.trim() : null,
                          },
                          { onSuccess: () => setCommitNote("") },
                        );
                      })();
                    }}
                  >
                    <Save size={13} aria-hidden />
                    {commit.isPending ? "Committing…" : "Commit version"}
                  </PrimaryButton>
                </div>
              </div>
              {commit.error != null && (
                <p className="text-body text-critical">
                  {isApiError(commit.error)
                    ? commit.error.message
                    : "Could not commit this version."}
                </p>
              )}
              {save.error != null && conflict === null && (
                <p className="text-body text-critical">
                  {isApiError(save.error)
                    ? save.error.message
                    : "Could not save. Your text is still on screen."}
                </p>
              )}
            </div>

            <DataBlockPanel
              bankId={bankId}
              cycleId={cycleId}
              blocks={blocksQuery.data?.blocks ?? []}
              blockTypes={blockTypesQuery.data?.blockTypes ?? []}
              attachments={attachmentsQuery.data?.attachments ?? []}
              expectedTypes={section.expectedBlockTypes}
              readOnly={!canEdit}
              onInsertBlock={(blockId) =>
                editorRef.current?.insertDataBlock(blockId)
              }
              onInsertFact={(blockId, factKey) =>
                editorRef.current?.insertFactRef(blockId, factKey)
              }
            />
          </div>

          {conflict && (
            <ConflictDialog
              conflict={conflict}
              onClose={() => setConflict(null)}
              onReload={() => {
                setConflict(null);
                void sectionQuery.refetch().then(() => {
                  setDocToken(`reload-${Date.now()}`);
                  setSaveState("idle");
                });
              }}
            />
          )}
        </IcaapBlocksProvider>
      )}
    </QueryBoundary>
  );
}
