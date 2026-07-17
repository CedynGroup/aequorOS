'use client';

/**
 * Transport modes explainer: the three T24 channels, what they share, and an
 * honest note on the portal-gated live-transport completion seam — the same
 * pattern as the market-data live vendor transports.
 */

import { GitBranch, Layers, Radio, ShieldCheck } from 'lucide-react';
import { MODES } from './shared';

export default function TransportModes() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        {MODES.map((mode) => (
          <div key={mode.key} className="card p-5 space-y-2">
            <div className="flex items-center gap-2">
              <Radio size={16} className="text-slate" aria-hidden />
              <h3 className="text-h3 text-navy">{mode.name}</h3>
            </div>
            <p className="text-caption font-medium text-slate">{mode.channel}</p>
            <p className="text-body text-slate leading-relaxed">{mode.description}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Layers size={16} className="text-slate" aria-hidden />
            <h3 className="text-h3 text-navy">One canonical model, any channel</h3>
          </div>
          <p className="mt-3 text-body text-slate leading-relaxed">
            The three modes reach the same canonical model — only the source vocabulary and
            network protocol differ. Positions, GL, counterparties, products, and off-balance
            exposures land identically, so every calculation module runs the same whether the
            data arrived over OFS, IRIS, or the Open APIs.
          </p>
        </div>
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={16} className="text-slate" aria-hidden />
            <h3 className="text-h3 text-navy">Credentials never leave the vault</h3>
          </div>
          <p className="mt-3 text-body text-slate leading-relaxed">
            Service credentials are encrypted at rest (AES-256-GCM) and decrypted only for a
            single sign-on cycle. Responses carry only the fingerprint, status, and expiry;
            raw core error text never reaches a bank-facing surface.
          </p>
        </div>
      </div>

      <div className="card p-5 border-l-4 border-l-action">
        <div className="flex items-center gap-2">
          <GitBranch size={16} className="text-action" aria-hidden />
          <h3 className="text-h3 text-navy">Live connectivity: one completion seam per mode</h3>
        </div>
        <p className="mt-2 text-body text-slate leading-relaxed">
          Everything from request building through extraction, translation, persistence,
          scheduling, and the credential lifecycle is complete and tested against recorded
          fixtures. The single remaining step for each mode is the live network submission,
          which is finalized with Temenos developer-portal access to the site&apos;s exact
          endpoint and token flow — the same pattern the market-data vendor transports use.
          Until then a connection can be configured, validated, and scheduled; scheduled pulls
          report an actionable &ldquo;core unavailable&rdquo; and retry.
        </p>
      </div>
    </div>
  );
}
