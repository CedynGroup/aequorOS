export default function ModuleCard({
  number,
  name,
  description,
  ai,
}: {
  number: string;
  name: string;
  description: string;
  ai: string;
}) {
  return (
    <article className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg px-8 py-10 h-full flex flex-col">
      <p className="font-serif text-accent text-lg">Module {number}</p>
      <h3 className="mt-2 font-serif font-bold text-navy text-2xl leading-snug">
        {name}
      </h3>
      <p className="mt-4 text-text-primary text-lg leading-relaxed">
        {description}
      </p>
      <p className="mt-6 text-text-muted text-sm italic leading-relaxed">
        {ai}
      </p>
    </article>
  );
}
