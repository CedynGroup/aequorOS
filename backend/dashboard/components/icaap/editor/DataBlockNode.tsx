"use client";

/**
 * The in-document rendering of a cycle data block.
 *
 * Nothing numeric is stored in the document: the card shows the figures the
 * binding carries right now, with the staleness the server evaluated. A block
 * the cycle no longer has is drawn as a visible gap, never silently omitted.
 */

import { NodeViewWrapper, type NodeViewProps } from "@tiptap/react";
import { Table2 } from "lucide-react";
import { useIcaapBlockById } from "../blocksContext";
import {
  blockStatusCopy,
  blockTitle,
  fmtDateValue,
  fmtFact,
  NOT_AVAILABLE,
} from "../format";

const TONE_CLASS: Record<string, string> = {
  ok: "text-success",
  warn: "text-warning",
  crit: "text-critical",
  neutral: "text-slate",
};

export default function DataBlockNode(props: NodeViewProps) {
  const blockId =
    typeof props.node.attrs.blockId === "string" ? props.node.attrs.blockId : null;
  const block = useIcaapBlockById(blockId);

  if (!block) {
    return (
      <NodeViewWrapper
        data-icaap-block=""
        className="my-3 card border-l-4 border-l-critical p-4"
      >
        <p className="text-body font-medium text-navy">Figure not available</p>
        <p className="mt-1 text-caption text-slate">
          This paragraph refers to a set of figures the cycle no longer carries.
          Add the block again, or remove the reference before committing.
        </p>
      </NodeViewWrapper>
    );
  }

  const asOf = block.currentBinding?.sourceAsOf
    ? fmtDateValue(block.currentBinding.sourceAsOf)
    : null;
  const status = blockStatusCopy(block.status, {
    asOf: asOf ?? undefined,
    pinReason: block.pinReason,
  });
  const facts = Object.values(block.currentBinding?.facts ?? {});

  return (
    <NodeViewWrapper
      data-icaap-block=""
      className={`my-3 card p-4 ${props.selected ? "ring-2 ring-action" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="inline-flex items-center gap-2 text-body font-medium text-navy">
            <Table2 size={14} aria-hidden />
            {blockTitle(block)}
          </p>
          <p className="mt-0.5 text-caption text-slate">
            <span className={TONE_CLASS[status.tone]}>{status.label}</span>
            {asOf ? ` · as at ${asOf}` : ""}
          </p>
        </div>
      </div>
      {facts.length > 0 ? (
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 md:grid-cols-3">
          {facts.map((fact) => (
            <div key={fact.key} className="min-w-0">
              <dt className="truncate text-caption text-slate">{fact.label}</dt>
              <dd className="text-body tnum font-medium text-navy">
                {fmtFact(fact)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-caption text-slate">{NOT_AVAILABLE}</p>
      )}
    </NodeViewWrapper>
  );
}
