import type { Metadata } from "next";
import { LinkButton } from "@/components/Button";
import PageHeader from "@/components/PageHeader";

export const metadata: Metadata = {
  title: "Security — AequorOS",
  description:
    "Tenant isolation enforced at the database, immutable lineage, reproducible regulatory returns, maker-checker e-signatures, and role-based access. The tour we give a bank's IT and audit teams.",
};

const facts = [
  {
    title: "Tenant isolation",
    body: "The database keeps each organization's data apart, so a bank user's session sees only their own organization. Searches are also limited to the bank's own records.",
    icon: (
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#0F1845"
        strokeWidth="1.7"
        aria-hidden
      >
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 9 H21 M9 9 V20" />
      </svg>
    ),
  },
  {
    title: "Immutable lineage",
    body: "Each figure traces to the load, batch, and timestamp that produced it. Corrections supersede prior records; nothing is silently overwritten.",
    icon: (
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#0F1845"
        strokeWidth="1.7"
        aria-hidden
      >
        <path d="M4 4 V20 H20" />
        <path d="M7 15 L11 10 L14 13 L19 6" />
      </svg>
    ),
  },
  {
    title: "Reproducible returns",
    body: "Sealed calculation runs are hashed on their input values. A past return can be regenerated exactly, figure by figure, in front of an examiner.",
    icon: (
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#0F1845"
        strokeWidth="1.7"
        aria-hidden
      >
        <path d="M12 3 L20 7 V12 C20 17 16.5 20 12 21 C7.5 20 4 17 4 12 V7 Z" />
        <path d="M9 12 L11.5 14.5 L15.5 9.5" />
      </svg>
    ),
  },
  {
    title: "Maker-checker signing",
    body: "Returns require a preparer and an approver, each signing under their own identity. Signed PDFs are certified so that any later change is detectable.",
    icon: (
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#0F1845"
        strokeWidth="1.7"
        aria-hidden
      >
        <path d="M4 18 C6 14 9 13 12 13 C15 13 18 14 20 18" />
        <circle cx="12" cy="8" r="3.5" />
        <path d="M17.5 5.5 L19 7 L22 4" />
      </svg>
    ),
  },
  {
    title: "Encryption",
    body: (
      <>
        Login details for connected services are kept in an encrypted vault.
        Stored files can be encrypted with a key we configure, and a bank can
        choose to encrypt its{" "}
        <a
          className="underline"
          href="https://github.com/CedynGroup/aequorOS/issues/352"
        >
          files
        </a>{" "}
        with its own key.
      </>
    ),
    icon: (
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#0F1845"
        strokeWidth="1.7"
        aria-hidden
      >
        <rect x="5" y="10" width="14" height="10" rx="2" />
        <path d="M8 10 V7 A4 4 0 0 1 16 7 V10" />
      </svg>
    ),
  },
  {
    title: "Access control",
    body: "Each user can reach only their assigned scope, and examiners get a read-only view. Selected business actions are recorded in an audit log that can be added to but never changed. Staff actions are recorded in a separate internal log.",
    icon: (
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#0F1845"
        strokeWidth="1.7"
        aria-hidden
      >
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 8 V12 L15 14" />
      </svg>
    ),
  },
];

export default function SecurityPage() {
  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16">
      <PageHeader
        kicker="Security & audit"
        title="Built to be examined."
        lede="A regulatory platform should welcome scrutiny. This page is the tour we give a bank's IT, risk, and audit teams, in the same words."
      />

      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 pb-16">
        {facts.map((fact) => (
          <div
            key={fact.title}
            className="bg-white border border-hairline rounded-md p-7 flex flex-col gap-3"
          >
            {fact.icon}
            <h2 className="text-lg font-semibold text-ink">{fact.title}</h2>
            <p className="text-[14.5px] leading-[1.65] text-ink-soft">
              {fact.body}
            </p>
          </div>
        ))}
      </div>

      <div className="border-t border-hairline pt-12 pb-24 flex flex-col md:flex-row md:items-center md:justify-between gap-8">
        <h2 className="font-serif font-medium text-[26px] md:text-[30px] leading-[1.15] tracking-tight">
          Ready for your IT team&apos;s questions.
        </h2>
        <LinkButton href="/contact" variant="primary" className="shrink-0">
          Request a demo
        </LinkButton>
      </div>
    </div>
  );
}
