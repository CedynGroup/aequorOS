import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET(): NextResponse {
  return NextResponse.json(
    { service: "aequoros-dashboard", status: "ok" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
