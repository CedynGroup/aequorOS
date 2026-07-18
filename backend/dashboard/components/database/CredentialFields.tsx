'use client';

/**
 * Write-only credential inputs for a direct database connection: service user,
 * password, and optional backend-specific secret material (JSON). Secrets
 * render as password inputs, nothing is ever pre-filled from the server, and
 * the only stored representation shown anywhere is the SHA-256 fingerprint.
 */

import { CREDENTIAL_FIELDS, type CredentialField, extraIsValid } from './shared';

export default function CredentialFields({
  values,
  onChange,
  idPrefix,
  fields = CREDENTIAL_FIELDS,
}: {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  idPrefix: string;
  /** The credential fields to render. Defaults to the full password-auth set;
   * key-pair backends (Snowflake) pass a service-user-only subset. */
  fields?: CredentialField[];
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {fields.map((field) => {
        const id = `${idPrefix}-${field.key}`;
        const value = values[field.key] ?? '';
        const extraInvalid = field.key === 'extra' && !extraIsValid(value);
        return (
          <div key={field.key} className={field.key === 'extra' ? 'sm:col-span-2' : ''}>
            <label
              htmlFor={id}
              className="block text-caption font-medium text-slate mb-1"
            >
              {field.label}
            </label>
            <input
              id={id}
              type={field.secret ? 'password' : 'text'}
              value={value}
              onChange={(event) => onChange(field.key, event.target.value)}
              placeholder={field.placeholder}
              autoComplete="off"
              className="w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
            />
            {extraInvalid ? (
              <p className="mt-1 text-caption text-critical">
                Must be a JSON object, e.g. {'{"wallet": "..."}'}.
              </p>
            ) : (
              field.hint && <p className="mt-1 text-caption text-slate">{field.hint}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
