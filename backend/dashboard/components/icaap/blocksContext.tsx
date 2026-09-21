"use client";

/**
 * The cycle's bound data blocks, for the editor's node views.
 *
 * A document stores only a block id; the FIGURES come from the binding. The
 * node views read them from this context so a refreshed block updates every
 * sentence that cites it, and a block the document references but the cycle no
 * longer has renders as an explicit gap rather than a stale number.
 */

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { IcaapDataBlockRead } from "@/lib/api/icaap";

const IcaapBlocksContext = createContext<ReadonlyMap<string, IcaapDataBlockRead>>(
  new Map(),
);

export function IcaapBlocksProvider({
  blocks,
  children,
}: {
  blocks: readonly IcaapDataBlockRead[];
  children: ReactNode;
}) {
  const byId = useMemo(
    () => new Map(blocks.map((block) => [block.id, block])),
    [blocks],
  );
  return (
    <IcaapBlocksContext.Provider value={byId}>
      {children}
    </IcaapBlocksContext.Provider>
  );
}

/** The block, or undefined when the cycle no longer carries it. */
export function useIcaapBlockById(
  blockId: string | null | undefined,
): IcaapDataBlockRead | undefined {
  const byId = useContext(IcaapBlocksContext);
  return blockId ? byId.get(blockId) : undefined;
}
