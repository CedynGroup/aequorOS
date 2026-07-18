'use client';

/**
 * Client wallet (mTLS) control — the friendly path for onboarding an Oracle
 * Autonomous Database connection. Oracle ADB requires mutual TLS: the operator
 * downloads a "Client Credentials" wallet (a ZIP holding `ewallet.pem`) and sets
 * a wallet password. This control lets them pick that file plus the password and
 * writes both into the connection's WRITE-ONLY credential `extra` object as
 * `oracle_wallet` (base64 of the ZIP, or the raw PEM text) and `wallet_password`.
 *
 * The backend Oracle driver reads those two keys, extracts `ewallet.pem`, and
 * negotiates mTLS. The material is write-only: it is vault-sealed on submit and
 * never returned, so nothing is ever rendered back — on rotation we only note
 * that a wallet may already be stored.
 */

import { useRef, useState } from 'react';
import { FileKey2, Loader2 } from 'lucide-react';

/** Read a file's raw text (used for a `.pem` upload). */
function fileToText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () =>
      reject(reader.error ?? new Error('Could not read the wallet file.'));
    reader.readAsText(file);
  });
}

/** Base64-encode a file's bytes via a data URL, then strip the `data:…;base64,`
 * prefix — the robust binary-safe path for a wallet ZIP. */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== 'string') {
        reject(new Error('Unexpected file read result.'));
        return;
      }
      const comma = result.indexOf(',');
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () =>
      reject(reader.error ?? new Error('Could not read the wallet file.'));
    reader.readAsDataURL(file);
  });
}

export default function OracleWalletFields({
  values,
  onChange,
  idPrefix,
  mode,
}: {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  idPrefix: string;
  /** required: onboarding a new connection. rotate: editing an existing one,
   * where a wallet may already be stored (but is never returned). */
  mode: 'required' | 'rotate';
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);

  const walletLoaded = Boolean(values.oracle_wallet?.trim());

  const handleFile = async (file: File | undefined) => {
    setReadError(null);
    if (!file) return;
    setReading(true);
    try {
      const isPem = /\.pem$/i.test(file.name);
      const material = isPem ? await fileToText(file) : await fileToBase64(file);
      if (!material.trim()) {
        throw new Error('The wallet file is empty.');
      }
      onChange('oracle_wallet', material);
      setFileName(file.name);
    } catch (error) {
      onChange('oracle_wallet', '');
      setFileName(null);
      setReadError(
        error instanceof Error ? error.message : 'Could not read the wallet file.',
      );
    } finally {
      setReading(false);
    }
  };

  const clearWallet = () => {
    onChange('oracle_wallet', '');
    setFileName(null);
    setReadError(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <fieldset className="rounded border border-border p-4 space-y-3">
      <legend className="px-1 text-caption font-medium uppercase tracking-wider text-slate">
        Client wallet (mTLS)
      </legend>
      <p className="text-caption text-slate">
        Required for Oracle Autonomous Database. Upload the Client Credentials
        wallet (.zip) or ewallet.pem, plus its wallet password. Stored encrypted
        and never displayed again.
      </p>

      <div>
        <label
          htmlFor={`${idPrefix}-file`}
          className="block text-caption font-medium text-slate mb-1"
        >
          Wallet file (.zip / .pem)
        </label>
        <input
          ref={inputRef}
          id={`${idPrefix}-file`}
          type="file"
          accept=".zip,.pem"
          onChange={(event) => void handleFile(event.target.files?.[0])}
          className="block text-body text-navy file:mr-3 file:px-3 file:py-1.5 file:rounded file:border file:border-border file:bg-surface file:text-caption file:font-medium file:text-navy hover:file:bg-border-light"
        />
        {reading ? (
          <p className="mt-1 inline-flex items-center gap-1.5 text-caption text-slate">
            <Loader2 size={12} className="animate-spin" aria-hidden />
            Reading wallet…
          </p>
        ) : walletLoaded ? (
          <p className="mt-1 inline-flex flex-wrap items-center gap-1.5 text-caption text-success">
            <FileKey2 size={12} aria-hidden />
            {fileName ? `${fileName} ready to upload` : 'Wallet ready to upload'}
            <button
              type="button"
              onClick={clearWallet}
              className="text-slate underline hover:text-navy"
            >
              clear
            </button>
          </p>
        ) : (
          mode === 'rotate' && (
            <p className="mt-1 text-caption text-slate">
              A wallet is stored — upload a new one to replace it. It is never
              displayed.
            </p>
          )
        )}
        {readError && <p className="mt-1 text-caption text-critical">{readError}</p>}
      </div>

      <div>
        <label
          htmlFor={`${idPrefix}-password`}
          className="block text-caption font-medium text-slate mb-1"
        >
          Wallet password
        </label>
        <input
          id={`${idPrefix}-password`}
          type="password"
          value={values.wallet_password ?? ''}
          onChange={(event) => onChange('wallet_password', event.target.value)}
          placeholder="The password set when the wallet was downloaded"
          autoComplete="off"
          className="w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
        />
      </div>
    </fieldset>
  );
}
