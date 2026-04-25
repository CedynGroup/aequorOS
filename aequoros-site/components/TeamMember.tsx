import Image from 'next/image';
import { Linkedin } from 'lucide-react';

export type TeamMemberData = {
  name: string;
  title: string;
  location?: string;
  image: string;
  bio: string[];
  linkedin?: string;
};

export default function TeamMember({ member }: { member: TeamMemberData }) {
  return (
    <div className="grid lg:grid-cols-[1fr,2fr] gap-10 lg:gap-16 items-start">
      <div>
        <div className="relative w-full max-w-[400px] aspect-square rounded-lg overflow-hidden border border-border-light bg-navy">
          <Image
            src={member.image}
            alt={`${member.name}, ${member.title} of AequorOS`}
            fill
            sizes="(max-width: 1024px) 100vw, 400px"
            className="object-cover"
            priority
          />
        </div>
      </div>

      <div>
        <h2 className="font-serif font-bold text-navy text-3xl">
          {member.name}
        </h2>
        <p className="mt-2 text-text-muted font-medium">{member.title}</p>
        {member.location && (
          <p className="mt-1 text-text-muted italic">{member.location}</p>
        )}

        <div className="mt-8 space-y-5 text-text-primary leading-relaxed">
          {member.bio.map((paragraph, i) => (
            <p key={i}>{paragraph}</p>
          ))}
        </div>

        {member.linkedin && (
          <a
            href={`https://${member.linkedin}`}
            target="_blank"
            rel="noreferrer"
            className="mt-8 inline-flex items-center gap-2 text-navy hover:text-accent transition-colors font-medium"
          >
            <Linkedin size={18} className="text-accent" />
            {member.linkedin}
          </a>
        )}
      </div>
    </div>
  );
}
