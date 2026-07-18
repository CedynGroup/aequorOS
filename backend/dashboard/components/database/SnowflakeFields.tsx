'use client';

/**
 * Snowflake warehouse control — the friendly path for onboarding a Snowflake
 * Database (Direct) connection. Snowflake is addressed by account + warehouse
 * (never host:port) and authenticates with a key pair (never a password): the
 * operator provisions a read-only user, registers its public key in Snowflake,
 * and pastes (or uploads) the matching PKCS#8 private key here.
 *
 * Two kinds of material are collected:
 *   - Non-secret warehouse config (account, warehouse, role, default schema,
 *     change-stream toggle) is threaded into the form's `snowflake` config,
 *     which the payload serializes into `connection_options.snowflake` with
 *     snake_case keys (`account`, `warehouse`, `role`, `default_schema`,
 *     `use_streams`).
 *   - The private key + optional passphrase are WRITE-ONLY credential material,
 *     written into the connection's credential `extra` object as
 *     `snowflake_private_key` and `private_key_passphrase`.
 *
 * The secret material is vault-sealed on submit and never returned, so nothing
 * is pre-filled on a rotation edit. Once a key is loaded, its text is never
 * rendered back — only a "key ready" chip is shown, exactly like the Oracle
 * wallet and password fields.
 */

import { useRef, useState } from 'react';
import { FileKey2, Loader2 } from 'lucide-react';
import type { SnowflakeConfig } from './ConnectionForm';

/** Read a private-key file's raw text (`.p8` / `.pem` / `.key` are PEM text). */
function fileToText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () =>
      reject(reader.error ?? new Error('Could not read the private key file.'));
    reader.readAsText(file);
  });
}

const inputClass =
  'w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono';
const labelClass = 'block text-caption font-medium text-slate mb-1';

export default function SnowflakeFields({
  config,
  onConfigChange,
  values,
  onCredChange,
  idPrefix,
  mode,
}: {
  config: SnowflakeConfig;
  onConfigChange: (patch: Partial<SnowflakeConfig>) => void;
  values: Record<string, string>;
  onCredChange: (key: string, value: string) => void;
  idPrefix: string;
  /** required: onboarding a new connection. rotate: editing an existing one,
   * where a key may already be stored (but is never returned). */
  mode: 'required' | 'rotate';
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);

  const keyLoaded = Boolean(values.snowflake_private_key?.trim());

  const handleFile = async (file: File | undefined) => {
    setReadError(null);
    if (!file) return;
    setReading(true);
    try {
      const material = await fileToText(file);
      if (!material.trim()) {
        throw new Error('The private key file is empty.');
      }
      onCredChange('snowflake_private_key', material);
      setFileName(file.name);
    } catch (error) {
      onCredChange('snowflake_private_key', '');
      setFileName(null);
      setReadError(
        error instanceof Error
          ? error.message
          : 'Could not read the private key file.',
      );
    } finally {
      setReading(false);
    }
  };

  const clearKey = () => {
    onCredChange('snowflake_private_key', '');
    setFileName(null);
    setReadError(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <fieldset className="rounded border border-border p-4 space-y-4">
      <legend className="px-1 text-caption font-medium uppercase tracking-wider text-slate">
        Snowflake warehouse
      </legend>
      <p className="text-caption text-slate">
        Snowflake is addressed by account and warehouse and authenticates with a
        key pair — no host, port, or password. Register the public key on the
        read-only Snowflake user, then provide its private key below.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor={`${idPrefix}-account`} className={labelClass}>
            Account
          </label>
          <input
            id={`${idPrefix}-account`}
            type="text"
            value={config.account}
            onChange={(event) => onConfigChange({ account: event.target.value })}
            placeholder="orgname-accountname"
            className={inputClass}
          />
          <p className="mt-1 text-caption text-slate">
            The Snowflake account identifier (e.g. org-account or the legacy
            locator).
          </p>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-warehouse`} className={labelClass}>
            Warehouse
          </label>
          <input
            id={`${idPrefix}-warehouse`}
            type="text"
            value={config.warehouse}
            onChange={(event) => onConfigChange({ warehouse: event.target.value })}
            placeholder="REPORTING_WH"
            className={inputClass}
          />
          <p className="mt-1 text-caption text-slate">
            The virtual warehouse that runs the extraction queries.
          </p>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-role`} className={labelClass}>
            Role (optional)
          </label>
          <input
            id={`${idPrefix}-role`}
            type="text"
            value={config.role}
            onChange={(event) => onConfigChange({ role: event.target.value })}
            placeholder="AEQUOROS_READONLY"
            className={inputClass}
          />
          <p className="mt-1 text-caption text-slate">
            The role to assume. Leave blank to use the user&apos;s default role.
          </p>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-default-schema`} className={labelClass}>
            Default schema (optional)
          </label>
          <input
            id={`${idPrefix}-default-schema`}
            type="text"
            value={config.defaultSchema}
            onChange={(event) =>
              onConfigChange({ defaultSchema: event.target.value })
            }
            placeholder="PUBLIC"
            className={inputClass}
          />
          <p className="mt-1 text-caption text-slate">
            The schema resolved when a query does not qualify one.
          </p>
        </div>
      </div>

      <label className="flex items-start gap-2 text-body text-navy">
        <input
          type="checkbox"
          checked={config.useStreams}
          onChange={(event) => onConfigChange({ useStreams: event.target.checked })}
          className="mt-1 rounded border-border"
        />
        <span>
          Use change streams
          <span className="block text-caption text-slate">
            Read incremental changes via Snowflake streams instead of a full
            table scan each sync.
          </span>
        </span>
      </label>

      <div className="space-y-3 border-t border-border pt-3">
        <p className="text-caption font-medium uppercase tracking-wider text-slate">
          Key-pair authentication
        </p>
        <div>
          <label htmlFor={`${idPrefix}-key`} className={labelClass}>
            Private key (PKCS#8 PEM)
          </label>
          {keyLoaded ? (
            <p className="inline-flex flex-wrap items-center gap-1.5 text-caption text-success">
              <FileKey2 size={12} aria-hidden />
              {fileName
                ? `${fileName} ready to upload`
                : 'Private key ready to upload'}
              <button
                type="button"
                onClick={clearKey}
                className="text-slate underline hover:text-navy"
              >
                clear
              </button>
            </p>
          ) : (
            <>
              <textarea
                id={`${idPrefix}-key`}
                value=""
                onChange={(event) =>
                  onCredChange('snowflake_private_key', event.target.value)
                }
                onPaste={(event) => {
                  event.preventDefault();
                  const pasted = event.clipboardData.getData('text');
                  if (pasted) onCredChange('snowflake_private_key', pasted);
                }}
                rows={4}
                placeholder={
                  '-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----'
                }
                autoComplete="off"
                spellCheck={false}
                className="w-full px-3 py-1.5 rounded border border-border text-caption text-navy font-mono"
              />
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <input
                  ref={inputRef}
                  id={`${idPrefix}-key-file`}
                  type="file"
                  accept=".p8,.pem,.key"
                  onChange={(event) => void handleFile(event.target.files?.[0])}
                  className="block text-body text-navy file:mr-3 file:px-3 file:py-1.5 file:rounded file:border file:border-border file:bg-surface file:text-caption file:font-medium file:text-navy hover:file:bg-border-light"
                />
                {reading && (
                  <span className="inline-flex items-center gap-1.5 text-caption text-slate">
                    <Loader2 size={12} className="animate-spin" aria-hidden />
                    Reading key…
                  </span>
                )}
              </div>
              <p className="mt-1 text-caption text-slate">
                Paste the PEM or upload a .p8 / .pem / .key file. Stored encrypted
                and never displayed again.
                {mode === 'rotate' &&
                  ' A key may already be stored — provide a new one to replace it.'}
              </p>
            </>
          )}
          {readError && (
            <p className="mt-1 text-caption text-critical">{readError}</p>
          )}
        </div>

        <div>
          <label htmlFor={`${idPrefix}-passphrase`} className={labelClass}>
            Private key passphrase (optional)
          </label>
          <input
            id={`${idPrefix}-passphrase`}
            type="password"
            value={values.private_key_passphrase ?? ''}
            onChange={(event) =>
              onCredChange('private_key_passphrase', event.target.value)
            }
            placeholder="Only if the private key is encrypted"
            autoComplete="off"
            className={inputClass}
          />
        </div>
      </div>
    </fieldset>
  );
}
