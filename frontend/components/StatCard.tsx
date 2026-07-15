export default function StatCard({
  number,
  label,
}: {
  number: string;
  label: string;
}) {
  return (
    <div className="bg-soft-bg border border-border-light border-l-4 border-l-accent rounded-lg p-6 md:p-7">
      <div className="font-serif font-bold text-navy text-4xl leading-tight">
        {number}
      </div>
      <p className="mt-3 text-sm font-medium text-text-primary leading-relaxed">
        {label}
      </p>
    </div>
  );
}
