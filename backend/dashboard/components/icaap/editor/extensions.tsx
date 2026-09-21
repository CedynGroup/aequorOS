"use client";

/**
 * The runtime extension set: the schema (`schema.ts`) plus the React node
 * views for the two custom nodes.
 *
 * The split is deliberate. `schema.ts` stays React-free so the parity test can
 * build the real ProseMirror schema in plain Node and compare it with the
 * backend allowlist; this file only attaches how those nodes are DRAWN, which
 * changes nothing about what the server will accept.
 */

import { ReactNodeViewRenderer } from "@tiptap/react";
import type { Extensions } from "@tiptap/core";
import { DataBlock, FactRef, icaapSchemaExtensions } from "./schema";
import DataBlockNode from "./DataBlockNode";
import FactRefNode from "./FactRefNode";

export function icaapEditorExtensions(): Extensions {
  const base = icaapSchemaExtensions();
  return base.map((extension) => {
    if (extension.name === DataBlock.name) {
      return DataBlock.extend({
        addNodeView() {
          return ReactNodeViewRenderer(DataBlockNode);
        },
      });
    }
    if (extension.name === FactRef.name) {
      return FactRef.extend({
        addNodeView() {
          return ReactNodeViewRenderer(FactRefNode);
        },
      });
    }
    return extension;
  });
}
