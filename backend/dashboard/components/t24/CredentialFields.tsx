'use client';

/**
 * Per-mode credential form fields (OFS service user, IRIS/Open API client
 * credentials). Values are write-only: secrets render as password inputs,
 * nothing is ever pre-filled from the server, and the only stored
 * representation shown anywhere is the SHA-256 fingerprint.
 */

import { CREDENTIAL_FIELDS, type ModeKey } from './shared';

export default function CredentialFields({
  mode,
  values,
  onChange,
  idPrefix,
}: {
  mode: ModeKey;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  idPrefix: string;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {CREDENTIAL_FIELDS[mode].map((field) => {
        const id = `${idPrefix}-${field.key}`;
        return (
          <div key={field.key}>
            <label
              htmlFor={id}
              className="block text-caption font-medium text-slate mb-1"
            >
              {field.label}
            </label>
            <input
              id={id}
              type={field.secret ? 'password' : 'text'}
              value={values[field.key] ?? ''}
              onChange={(event) => onChange(field.key, event.target.value)}
              placeholder={field.placeholder}
              autoComplete="off"
              className="w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
            />
            {field.hint && (
              <p className="mt-1 text-caption text-slate">{field.hint}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
