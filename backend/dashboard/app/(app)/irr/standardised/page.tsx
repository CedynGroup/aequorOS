"use client";

/**
 * IRRBB · Standardised Framework.
 *
 * A sibling of the other IRRBB tabs rather than a revision of them: the
 * framework is a new, prescribed scenario set with its own vocabulary and its
 * own outlier test, and the legacy engine behind `/irr` is untouched by it.
 * The workspace component owns every read, refusal and empty state.
 */

import StandardisedFramework from "@/components/irr/StandardisedFramework";

export default function IrrStandardisedFrameworkPage() {
  return <StandardisedFramework />;
}
