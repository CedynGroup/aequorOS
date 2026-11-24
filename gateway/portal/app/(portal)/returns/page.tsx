import type { Metadata } from "next";
import type { SearchParams } from "@/lib/search-params";
import { ReturnsView } from "./view";

export const metadata: Metadata = { title: "Return definitions" };

export default async function ReturnsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return <ReturnsView params={await searchParams} selected={null} />;
}
