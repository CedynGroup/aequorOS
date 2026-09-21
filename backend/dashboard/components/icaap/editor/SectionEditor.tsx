"use client";

/**
 * The ICAAP narrative editor.
 *
 * THE ONLY MODULE THAT IMPORTS TIPTAP. It is reached exclusively through
 * `SectionEditorLoader`, which imports it dynamically with `ssr: false`, so
 * ProseMirror never enters the Command Center's initial bundle
 * (`scripts/assert-home-route-bundle.mjs` proves both halves of that claim).
 *
 * Paste is safe by construction: ProseMirror parses pasted HTML only through
 * the `parseHTML` rules of allowed nodes, so links, images, tables and script
 * content are dropped — and the server re-validates the document anyway.
 */

import { useEffect } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import type { JSONContent } from "@tiptap/core";
import { icaapEditorExtensions } from "./extensions";
import { emptyIcaapDoc } from "./schema";
import Toolbar from "./Toolbar";
import type { ProseMirrorDoc } from "@/lib/api/icaap";

export type SectionEditorHandle = {
  insertDataBlock: (blockId: string) => void;
  insertFactRef: (blockId: string, factKey: string) => void;
  focus: () => void;
};

export type SectionEditorProps = {
  initialDoc: ProseMirrorDoc | null;
  /** Changes when the document must be REPLACED (a reload after a conflict). */
  docToken?: string;
  readOnly?: boolean;
  onChange: (doc: ProseMirrorDoc) => void;
  /**
   * Hands the insertion commands to the workspace.
   *
   * A callback, not a ref: `next/dynamic` consumes the ref of the component it
   * wraps for its own retry handle and never forwards it, so a ref passed
   * through the loader would silently arrive as the loader's own object.
   */
  onReady?: (handle: SectionEditorHandle | null) => void;
  onInsertBlockRequest?: () => void;
  onInsertFactRequest?: () => void;
};

/**
 * The API carries the document as opaque JSON (the backend validates it
 * against its own allowlist, which is the authority). Tiptap wants its
 * `JSONContent` shape, which is the same object with a narrower type.
 */
function asContent(doc: ProseMirrorDoc | null): JSONContent {
  return (doc ?? emptyIcaapDoc()) as JSONContent;
}

export default function SectionEditor({
  initialDoc,
  docToken,
  readOnly = false,
  onChange,
  onReady,
  onInsertBlockRequest,
  onInsertFactRequest,
}: SectionEditorProps) {
  const editor = useEditor({
    extensions: icaapEditorExtensions(),
    content: asContent(initialDoc),
    editable: !readOnly,
    // This component renders on the client only; telling Tiptap so avoids the
    // hydration-mismatch warning it otherwise emits.
    immediatelyRender: false,
    editorProps: {
      attributes: {
        // Prose styling stays inside this module (no typography plugin, and
        // no global stylesheet edit) so the editor is self-contained.
        class: [
          "min-h-[24rem] px-4 py-3 text-body text-ink focus:outline-none",
          "[&_p]:mb-3 [&_p:last-child]:mb-0",
          "[&_h2]:text-h2 [&_h2]:text-navy [&_h2]:mt-5 [&_h2]:mb-2",
          "[&_h3]:text-h3 [&_h3]:text-navy [&_h3]:mt-4 [&_h3]:mb-2",
          "[&_h4]:text-body [&_h4]:font-medium [&_h4]:text-navy [&_h4]:mt-3 [&_h4]:mb-1",
          "[&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-6",
          "[&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-6",
          "[&_li]:mb-1",
          "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-4 [&_blockquote]:text-slate",
        ].join(" "),
        "aria-label": "Section narrative",
      },
    },
    onUpdate: ({ editor: instance }) => {
      onChange(instance.getJSON() as ProseMirrorDoc);
    },
  });

  useEffect(() => {
    editor?.setEditable(!readOnly);
  }, [editor, readOnly]);

  // Replace the document only on an explicit token change (a conflict
  // reload). Mirroring every server response back into the editor would
  // fight the author's cursor on each autosave.
  useEffect(() => {
    if (!editor || docToken === undefined) return;
    editor.commands.setContent(asContent(initialDoc), {
      emitUpdate: false,
    });
    // `initialDoc` is deliberately not a dependency: the token is the signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docToken, editor]);

  useEffect(() => {
    if (!onReady) return;
    if (!editor) {
      onReady(null);
      return;
    }
    onReady({
      insertDataBlock: (blockId: string) => {
        editor
          .chain()
          .focus()
          .insertContent({ type: "dataBlock", attrs: { blockId } })
          .run();
      },
      insertFactRef: (blockId: string, factKey: string) => {
        editor
          .chain()
          .focus()
          .insertContent({
            type: "factRef",
            attrs: { blockId, factKey, suggestionId: null },
          })
          .run();
      },
      focus: () => {
        editor.chain().focus().run();
      },
    });
    return () => onReady(null);
  }, [editor, onReady]);

  if (!editor) {
    return (
      <div
        className="min-h-[24rem] animate-pulse rounded bg-surface"
        aria-busy="true"
        aria-label="Loading the editor"
      />
    );
  }

  return (
    <div className="card overflow-hidden">
      <Toolbar
        editor={editor}
        disabled={readOnly}
        onInsertBlock={readOnly ? undefined : onInsertBlockRequest}
        onInsertFact={readOnly ? undefined : onInsertFactRequest}
      />
      <EditorContent editor={editor} />
    </div>
  );
}
