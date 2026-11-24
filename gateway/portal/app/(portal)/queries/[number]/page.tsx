import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { QUERY_NUMBER, type SearchParams } from "@/lib/search-params";
import { QueriesView } from "../view";

type Props = { params: Promise<{ number: string }>; searchParams: Promise<SearchParams> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: (await params).number };
}

export default async function QueryPage({ params, searchParams }: Props) {
  const number = decodeURIComponent((await params).number);
  if (!QUERY_NUMBER.test(number)) notFound();
  return <QueriesView params={await searchParams} selected={number} />;
}
