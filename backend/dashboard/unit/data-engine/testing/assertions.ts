import { expect } from "vitest";
import { referenceRowTotal } from "../../../components/data-engine/shared";
import type {
  CanonicalPositionListRead,
  IngestionBatchRead,
  PositionSnapshotRead,
} from "@aequoros/risk-service-api";

/** Exact identity and stored Decimal strings; no financial calculation tolerance. */
export const canonicalTolerance = {
  absolute: 0,
  unit: "identity / canonical Decimal strings",
} as const;
export function assertCanonicalGraph(
  batch: IngestionBatchRead,
  list: CanonicalPositionListRead,
  snapshots: PositionSnapshotRead[],
) {
  expect(list.bankId, "Canonical: list belongs to another batch bank").toBe(
    batch.bankId,
  );
  expect(
    list.positions,
    "Canonical: accepted count differs from position count",
  ).toHaveLength(batch.recordsAccepted - referenceRowTotal(batch));
  expect(snapshots, "Canonical: snapshot cardinality differs").toHaveLength(
    list.positions.length,
  );
  const ids = new Set(list.positions.map((row) => row.id));
  expect(ids.size, "Canonical: duplicate position IDs").toBe(
    list.positions.length,
  );
  for (const position of list.positions) {
    const snapshot = snapshots.find((row) => row.id === position.snapshotId);
    expect(snapshot, "Canonical: snapshot link is dangling").toBeDefined();
    expect(
      snapshot?.positionId,
      "Canonical: snapshot points at another position",
    ).toBe(position.id);
    expect(snapshot?.lineageId, "Canonical: snapshot provenance differs").toBe(
      position.lineageId,
    );
    expect(snapshot?.balance, "Canonical: stored balance changed").toBe(
      position.balance,
    );
    expect(
      snapshot?.asOfDate.toISOString(),
      "Canonical: snapshot date differs",
    ).toBe(batch.asOfDate.toISOString());
  }
}
