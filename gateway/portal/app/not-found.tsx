import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto my-12 box-border flex max-w-lg flex-col gap-3 rounded-[10px] border border-line bg-white px-6 py-6">
      <h1 className="m-0 text-[22px] font-semibold">Not found</h1>
      <p className="m-0 text-[14px] text-ink-2">There is no page at this address.</p>
      <Link href="/" className="text-[14px] font-semibold">Go to the overview</Link>
    </main>
  );
}
