import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { SearchParams } from "@/lib/search-params";
import { ReturnsView } from "../view";

type Props = { params: Promise<{ code: string }>; searchParams: Promise<SearchParams> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: (await params).code };
}

export default async function ReturnPage({ params, searchParams }: Props) {
  const code = decodeURIComponent((await params).code);
  if (!/^[A-Z0-9][A-Z0-9-]{0,31}$/.test(code)) notFound();
  return <ReturnsView params={await searchParams} selected={code} />;
}
