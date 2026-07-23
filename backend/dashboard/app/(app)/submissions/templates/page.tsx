'use client';

/**
 * Regulatory Reporting — Templates. The return-template registry: one card
 * per registered return with its directive citation, frequency + deadline
 * rule, honest fidelity grade (tooltip explains the grade), section list,
 * and default channel. Everything cites the BoG research dossiers; nothing
 * invented is passed off as official.
 */

import { BookOpenCheck, CalendarDays, Send } from 'lucide-react';
import type { ReturnTemplateRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import {
  CHANNEL_LABELS,
  DEADLINE_RULE_TEXT,
  FAMILY_LABELS,
  FIDELITY_INFO,
  FidelityPill,
  TEMPLATE_SECTIONS,
} from '@/components/submissions/shared';
import { useReturnTemplates } from '@/lib/api/hooks';
import { centralBankName } from '@/lib/format';

export default function TemplatesPage() {
  const query = useReturnTemplates();
  const templates = query.data?.templates ?? [];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting', href: '/submissions' },
          { label: 'Templates' },
        ]}
        title="Return templates"
        subtitle="The regulator return registry — citations, deadlines, fidelity grades, and rendering layouts"
      />

      <div className="px-8 py-6 space-y-6">
        <div className="card px-5 py-4 flex items-start gap-3">
          <BookOpenCheck size={16} className="text-action shrink-0 mt-0.5" aria-hidden />
          <p className="text-caption text-navy/80 leading-relaxed">
            Template structures, citations, deadlines and fidelity grades
            follow the {centralBankName()} research dossiers
            (<span className="font-mono">docs/research/bog_returns_and_templates.md</span>,{' '}
            <span className="font-mono">docs/research/bog_orass_submission_channels.md</span>).
            Grades are honest: <strong>CONFIRMED</strong> structures are
            transcribed from published appendices, <strong>PARTIAL</strong>{' '}
            returns are directive-described with non-public appendices, and{' '}
            <strong>REPRESENTATIVE</strong> layouts are professional
            reconstructions awaiting the official forms.
          </p>
        </div>

        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          skeleton={
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
            </div>
          }
        >
          {templates.length === 0 ? (
            <EmptyState
              title="No templates registered"
              description="The return registry is empty — check the risk service."
            />
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {templates.map((template) => (
                <TemplateCard key={template.code} template={template} />
              ))}
            </div>
          )}
        </QueryBoundary>
      </div>
    </>
  );
}

function TemplateCard({ template }: { template: ReturnTemplateRead }) {
  const sections = TEMPLATE_SECTIONS[template.templateId] ?? [];
  const deadline = DEADLINE_RULE_TEXT[template.code];
  const fidelity = FIDELITY_INFO[template.fidelity];

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <span className="font-mono">{template.code}</span>
            <span className="inline-flex items-center px-2 py-0.5 rounded border border-border bg-surface text-caption font-normal text-slate normal-case tracking-normal">
              {FAMILY_LABELS[template.family] ?? template.family}
            </span>
          </span>
        }
        subtitle={template.title}
        action={<FidelityPill fidelity={template.fidelity} />}
      />
      <CardBody className="space-y-4">
        <p className="text-caption text-navy/80 leading-relaxed">
          {template.directiveCitation}
        </p>

        <div className="flex items-start gap-2 text-caption text-navy/85">
          <CalendarDays size={13} className="text-slate shrink-0 mt-0.5" aria-hidden />
          <span className="leading-relaxed">
            <span className="capitalize font-medium">{template.frequency}</span>
            {deadline ? ` — ${deadline}` : ''}
          </span>
        </div>

        <div>
          <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
            Sections ({sections.length})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {sections.map((section) => (
              <span
                key={section}
                className="inline-flex items-center px-2 py-0.5 rounded border border-border-light bg-surface text-caption text-navy/85"
              >
                {section}
              </span>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 flex-wrap pt-1 border-t border-border-light">
          <span
            className="inline-flex items-center gap-1.5 text-caption text-slate"
            title={fidelity.blurb}
          >
            Fidelity: {template.fidelity} — hover for what this grade means
          </span>
          <span className="inline-flex items-center gap-1.5 text-caption text-navy/85">
            <Send size={12} className="text-slate" aria-hidden />
            Default channel: {CHANNEL_LABELS[template.defaultChannel]}
          </span>
        </div>
        <p className="font-mono text-micro text-slate">
          {template.templateId} · regulator {template.regulator} · generator{' '}
          {template.generator}
        </p>
      </CardBody>
    </Card>
  );
}
