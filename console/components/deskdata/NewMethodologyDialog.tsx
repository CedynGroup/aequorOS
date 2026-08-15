'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { createDeskMethodology, type DeskMethodology } from '@/lib/api';
import { useMutation } from '@/lib/use-api';
import { CeremonyBanner } from '@/components/curves';
import { Button, Field, Input, Modal, Textarea } from '@/components/ui';

/**
 * Create-a-new-methodology-code flow (the `createDeskMethodology` client had no
 * UI). Registers a brand-new code at v1 DRAFT with a documented rationale and a
 * full parameter set. Like every register write it is a governed event: the
 * draft still has to clear Track-2 approval by a second operator before any
 * determination can bind to it — hence the ceremony banner.
 */
export function NewMethodologyDialog({
  open,
  onClose,
  onCreated,
  templateParameters,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (created: DeskMethodology) => void;
  /** Optional prefill for the parameters JSON (e.g. clone an existing code). */
  templateParameters?: Record<string, unknown>;
}) {
  const formId = useId();
  const [code, setCode] = useState('');
  const [rationale, setRationale] = useState('');
  const [paramsText, setParamsText] = useState('{}');

  useEffect(() => {
    if (!open) return;
    setCode('');
    setRationale('');
    setParamsText(templateParameters ? JSON.stringify(templateParameters, null, 2) : '{}');
  }, [open, templateParameters]);

  const paramsParse = useMemo(():
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string } => {
    const text = paramsText.trim();
    if (text === '') return { ok: true, value: {} };
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Parameters must be a JSON object.' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : 'Invalid JSON.' };
    }
  }, [paramsText]);

  const create = useMutation(createDeskMethodology, {
    successMessage: (row) => `Registered ${row.methodology_code} v${row.version} (draft)`,
    errorContext: 'Register methodology',
    onSuccess: (row) => {
      onCreated(row);
      onClose();
    },
  });

  const valid = code.trim() !== '' && rationale.trim() !== '' && paramsParse.ok;

  function submit() {
    if (!valid || !paramsParse.ok) return;
    void create.mutate({
      methodology_code: code.trim(),
      parameters: paramsParse.value,
      change_rationale: rationale.trim(),
    });
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Register a new methodology code"
      description="Drafts version 1 — a second operator must approve it (Track 2) before any determination can use it."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={formId} loading={create.loading} disabled={!valid}>
            Register v1 draft
          </Button>
        </>
      }
    >
      <form
        id={formId}
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <CeremonyBanner>
          <p className="font-medium text-navy">New methodology code — a governed register write</p>
          <p className="mt-1">
            This creates the code at v1 in <span className="font-medium">draft</span>. It carries a
            documented rationale and is dual-controlled: approval by a distinct operator is required
            before it governs any run.
          </p>
        </CeremonyBanner>

        <Field label="Methodology code" required hint="Stable identifier, e.g. AEQ-GHS-CREDIT">
          <Input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="AEQ-GHS-CREDIT"
            className="font-mono"
            spellCheck={false}
          />
        </Field>

        <Field
          label="Change rationale"
          required
          hint="Recorded in the register — why this methodology exists."
        >
          <Textarea
            rows={2}
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            placeholder="Purpose and calibration basis for the new methodology."
          />
        </Field>

        <Field
          label="Parameters (JSON object)"
          error={!paramsParse.ok ? `JSON error: ${paramsParse.error}` : undefined}
          hint={paramsParse.ok ? 'The full versioned parameter set for this methodology.' : undefined}
        >
          <Textarea
            rows={14}
            spellCheck={false}
            className="font-mono text-caption"
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
          />
        </Field>
      </form>
    </Modal>
  );
}
