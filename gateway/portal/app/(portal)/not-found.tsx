import Link from "next/link";
import { button } from "@/components/ui";

export default function NotFound() {
  return (
    <section className="flex flex-col gap-3 rounded-[10px] border border-line bg-white px-6 py-6">
      <h1 className="m-0 text-[22px] font-semibold">Not found</h1>
      <p className="m-0 text-[14px] text-ink-2">There is no record at this address. Check the receipt, query number or code, or start from the overview.</p>
      <div>
        <Link href="/" className={button.secondary}>Go to the overview</Link>
      </div>
    </section>
  );
}
