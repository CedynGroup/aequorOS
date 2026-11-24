import type { Metadata } from "next";
import type { SearchParams } from "@/lib/search-params";
import { QueriesView } from "./view";

export const metadata: Metadata = { title: "Queries" };

export default async function QueriesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return <QueriesView params={await searchParams} selected={null} />;
}
