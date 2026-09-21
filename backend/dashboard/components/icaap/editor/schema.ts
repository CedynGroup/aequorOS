/**
 * The ICAAP editor's document schema — the CLIENT half of a contract whose
 * authority is the backend.
 *
 * `backend/app/domain/icaap/editor_schema.json` is the allowlist the server
 * validates every saved document against. This file must produce exactly that
 * node/mark set, with the same content expressions, groups, inline/atom flags
 * and attribute NAMES, or an author can compose a paragraph the API refuses.
 * `schema.parity.test.ts` compares the two and fails on any drift.
 *
 * Deliberately React-free so the parity test can build the real ProseMirror
 * schema in plain Node. The React node views live in `extensions.tsx`.
 *
 * Three rules worth stating because they are easy to lose in a dependency bump:
 *
 *  - StarterKit v3 ships Link, Code, CodeBlock, Strike and HorizontalRule ON.
 *    Every one of them is a node or mark the server does not accept, so they
 *    are disabled here. Leaving one enabled breaks parity (and the test).
 *  - Paragraphs carry a nullable `aiSuggestionId` from P1 (D-028), so P4's
 *    provenance does not need a document migration later. It is null for
 *    everything a human writes.
 *  - The top node is `block*`, not Tiptap's default `block+`. The server must
 *    be able to store a section nobody has written yet, so the two schemas
 *    would otherwise disagree about the emptiest document that exists.
 */

import {
  Extension,
  Node,
  mergeAttributes,
  type Extensions,
  type JSONContent,
} from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";

export const ICAAP_EDITOR_SCHEMA_VERSION = "icaap-editor-v1";

/** Heading levels the framework allows: the section title is the level 1. */
export const ICAAP_HEADING_LEVELS = [2, 3, 4] as const;

/**
 * A cycle data block, rendered inline in the narrative as a figure.
 *
 * Atomic and attribute-only: the block's CONTENT is never stored in the
 * document. It is resolved from the binding at read time, so a document can
 * never disagree with the figures the cycle actually bound.
 */
export const DataBlock = Node.create({
  name: "dataBlock",
  group: "block",
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      blockId: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute("data-block-id"),
        renderHTML: (attributes: Record<string, unknown>) => ({
          "data-block-id": attributes.blockId,
        }),
      },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-icaap-block]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-icaap-block": "" })];
  },
});

/**
 * A reference to ONE fact of a bound block.
 *
 * The figure is never typed into the prose: the chip renders the value the
 * binding carries, so refreshing a block updates every sentence that cites it
 * and a withdrawn source is visible in the text rather than frozen into it.
 */
export const FactRef = Node.create({
  name: "factRef",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      blockId: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute("data-block-id"),
        renderHTML: (attributes: Record<string, unknown>) => ({
          "data-block-id": attributes.blockId,
        }),
      },
      factKey: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute("data-fact-key"),
        renderHTML: (attributes: Record<string, unknown>) => ({
          "data-fact-key": attributes.factKey,
        }),
      },
      suggestionId: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute("data-suggestion-id"),
        renderHTML: (attributes: Record<string, unknown>) =>
          attributes.suggestionId
            ? { "data-suggestion-id": attributes.suggestionId }
            : {},
      },
    };
  },
  parseHTML() {
    return [{ tag: "span[data-icaap-fact]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { "data-icaap-fact": "" })];
  },
});

/**
 * D-028: the nullable paragraph provenance attribute, present from P1 so a
 * later AI-drafting phase adds no schema migration of stored documents.
 * Added as a global attribute rather than by extending Paragraph, because
 * StarterKit owns the Paragraph instance.
 */
export const AiSuggestionAttribute = Extension.create({
  name: "icaapAiSuggestionAttribute",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph"],
        attributes: {
          aiSuggestionId: {
            default: null,
            parseHTML: (element: HTMLElement) =>
              element.getAttribute("data-ai-suggestion-id"),
            renderHTML: (attributes: Record<string, unknown>) =>
              attributes.aiSuggestionId
                ? { "data-ai-suggestion-id": attributes.aiSuggestionId }
                : {},
          },
        },
      },
    ];
  },
});

/**
 * The top node, replacing StarterKit's Document.
 *
 * Tiptap's default is `block+`; the allowlist says `block*`, so that the
 * server can store a section nobody has written yet. StarterKit's Document is
 * therefore switched off and this one supplied instead — `extendNodeSchema`
 * cannot change a content expression, and the backend file is the authority,
 * never the other way round (`editor_schema.json` `parity_notes.doc_content`).
 */
export const Document = Node.create({
  name: "doc",
  topNode: true,
  content: "block*",
});

/** The schema-bearing extension set. No React, no node views. */
export function icaapSchemaExtensions(): Extensions {
  return [
    StarterKit.configure({
      // Replaced by `Document` below, which allows an empty section.
      document: false,
      heading: { levels: [...ICAAP_HEADING_LEVELS] },
      // Not in the server allowlist — every one of these is a node or mark
      // `validate_doc` rejects.
      code: false,
      codeBlock: false,
      strike: false,
      horizontalRule: false,
      link: false,
    }),
    Document,
    AiSuggestionAttribute,
    DataBlock,
    FactRef,
  ];
}

/** An empty document, for a section that has never been written. */
export function emptyIcaapDoc(): JSONContent {
  return { type: "doc", content: [{ type: "paragraph" }] };
}
