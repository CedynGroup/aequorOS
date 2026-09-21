"use client";

/**
 * A figure cited inside a sentence.
 *
 * The number is NOT typed into the prose. The chip renders the value the
 * block's current binding carries, tinted by the staleness the server
 * evaluated — so a refreshed block updates every sentence that cites it, and a
 * withdrawn source is visible in the text instead of frozen into it.
 */

import { NodeViewWrapper, type NodeViewProps } from "@tiptap/react";
import { useIcaapBlockById } from "../blocksContext";
import { blockStatusCopy, blockTitle, fmtFact, NOT_AVAILABLE } from "../format";

const TONE_CLASS: Record<string, string> = {
  ok: "bg-action-light text-action border-action/20",
  warn: "bg-warning-light text-warning border-warning/20",
  crit: "bg-critical-light text-critical border-critical/20",
  neutral: "bg-surface text-slate border-border",
};

export default function FactRefNode(props: NodeViewProps) {
  const blockId =
    typeof props.node.attrs.blockId === "string" ? props.node.attrs.blockId : null;
  const factKey =
    typeof props.node.attrs.factKey === "string" ? props.node.attrs.factKey : null;
  const block = useIcaapBlockById(blockId);
  const fact = factKey ? block?.currentBinding?.facts?.[factKey] : undefined;
  const status = block
    ? blockStatusCopy(block.status, { pinReason: block.pinReason })
    : { label: "Figure not available", tone: "crit" as const };

  return (
    <NodeViewWrapper as="span" data-icaap-fact="" className="inline-block align-baseline">
      <span
        title={`${block?.title ?? "Unlinked figure"} · ${status.label}`}
        className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-body tnum font-medium ${
          TONE_CLASS[status.tone]
        } ${props.selected ? "ring-2 ring-action" : ""}`}
      >
        {fact ? fmtFact(fact) : NOT_AVAILABLE}
      </span>
    </NodeViewWrapper>
  );
}
