"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BankEncryptionApi,
  type BankKeyConnect,
} from "@aequoros/risk-service-api";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useBankContext } from "@/components/shell/BankContext";
import { hasAccountAdministrationAuthority } from "@/lib/api/accountAdministration";
import { apiCall, configuration } from "@/lib/api/client";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import CopyButton from "@/components/ui/CopyButton";
import StatusPill from "@/components/ui/StatusPill";

const api = new BankEncryptionApi(configuration);
const inputClass =
  "min-w-0 w-full rounded border border-border bg-surface-base px-3 py-2 text-body text-navy";
const buttonClass =
  "rounded border border-border px-3 py-2 text-body font-medium text-action disabled:opacity-50";
const dateLabel = (value: Date | null | undefined) =>
  value ? value.toLocaleString() : "No recorded use";

export default function BankKeyPanel() {
  const { effectiveAuthority } = useUserProfile();
  const { bank } = useBankContext();
  if (!bank || !hasAccountAdministrationAuthority(effectiveAuthority))
    return null;
  return <BankKeySetup key={bank.id} bankId={bank.id} />;
}

export function BankKeySetup({ bankId }: { bankId: string }) {
  const cache = useQueryClient();
  const queryKey = ["bank-key", bankId];
  const status = useQuery({
    queryKey,
    queryFn: () => apiCall(() => api.getBankKeyStatus({ bankId })),
    retry: false,
  });
  const [keyId, setKeyId] = useState("");
  const [region, setRegion] = useState("");
  const [account, setAccount] = useState("");
  const [reason, setReason] = useState("");
  const [period, setPeriod] = useState(365);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [tested, setTested] = useState("");
  const data = status.data;
  const payload: BankKeyConnect = {
    provider: "aws_kms",
    keyId: keyId.trim(),
    region: region.trim(),
    ownerAccount: account.trim(),
  };
  const identity = JSON.stringify(payload);
  const valid =
    /^arn:aws(?:-us-gov|-cn)?:kms:[^:]+:\d{12}:key\/.+$/.test(payload.keyId) &&
    /^\d{12}$/.test(payload.ownerAccount) &&
    payload.region.length > 0;
  const canSave =
    valid &&
    tested === identity &&
    (!data?.connected || reason.trim().length >= 10);
  const role = data?.workloadRoleArn;
  const statement = JSON.stringify(
    {
      Sid: "AllowAequorOSKeyUse",
      Effect: "Allow",
      Principal: { AWS: role },
      Action: [
        "kms:DescribeKey",
        "kms:GenerateDataKey",
        "kms:Encrypt",
        "kms:Decrypt",
      ],
      Resource: "*",
    },
    null,
    2,
  );
  const connected = data?.key;
  const command = connected
    ? `aws kms enable-key-rotation --key-id '${connected.keyId}' --region '${connected.region}' --rotation-period-in-days ${period}`
    : "";

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The request failed. Retry the operation.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="min-w-0 lg:col-span-2">
      <CardHeader
        className="flex-wrap"
        title="Bank encryption key"
        subtitle="Administrator controls · the bank retains custody of its master key"
        action={
          <StatusPill tone={data?.connected ? "success" : "slate"}>
            {status.isPending
              ? "Loading…"
              : data?.connected
                ? "Connected"
                : "Not connected"}
          </StatusPill>
        }
      />
      <CardBody className="min-w-0 space-y-6">
        {status.isError ? (
          <div role="alert">
            <p>Could not load key status: {status.error.message}</p>
            <button
              className={buttonClass}
              onClick={() => void status.refetch()}
            >
              Retry status
            </button>
          </div>
        ) : status.isPending ? (
          <p>Loading key configuration…</p>
        ) : (
          <>
            <dl className="grid gap-3 text-body sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-slate">Current key version</dt>
                <dd className="break-all font-mono text-caption">
                  {data?.currentVersion ?? "Not connected"}
                </dd>
              </div>
              <div>
                <dt className="text-slate">Last recorded successful key use</dt>
                <dd>{dateLabel(data?.lastSuccessfulUseAt)}</dd>
              </div>
              <div>
                <dt className="text-slate">Last replacement-key rotation</dt>
                <dd>
                  {data?.lastRotatedAt
                    ? dateLabel(data.lastRotatedAt)
                    : "No recorded rotation"}
                </dd>
              </div>
              <div>
                <dt className="text-slate">Last checked state</dt>
                <dd>
                  {connected
                    ? `${connected.status} · ${dateLabel(connected.checkedAt)}`
                    : "Not connected"}
                </dd>
              </div>
            </dl>
            {connected && (
              <div className="space-y-2">
                <p className="break-all text-caption font-mono">
                  {connected.keyId}
                </p>
                <button
                  disabled={busy}
                  className={buttonClass}
                  onClick={() =>
                    void perform(async () => {
                      const result = await apiCall(() =>
                        api.testBankKeyConnection({
                          bankId,
                          bankKeyConnect: {
                            provider: "aws_kms",
                            keyId: connected.keyId,
                            ownerAccount: connected.ownerAccount,
                            region: connected.region,
                          },
                        }),
                      );
                      if (result.passed) setMessage(result.message);
                      else setError(result.message);
                      await cache.invalidateQueries({ queryKey });
                    })
                  }
                >
                  Test connected key
                </button>
              </div>
            )}
            <fieldset disabled={busy} className="min-w-0 space-y-4">
              <legend className="text-h3 text-navy">
                {data?.connected
                  ? "Rotate to a replacement key"
                  : "Connect your bank key"}
              </legend>
              <p className="text-body text-slate">
                1. Create a customer-managed symmetric AWS KMS key in the bank’s
                account. Keep the master key in AWS; enter only its exact ARN
                below.
              </p>
              <div className="grid gap-3 md:grid-cols-3">
                <label className="text-caption text-slate">
                  Exact key ARN
                  <input
                    className={inputClass}
                    value={keyId}
                    onChange={(e) => setKeyId(e.target.value)}
                    placeholder="arn:aws:kms:region:account:key/id"
                    autoComplete="off"
                  />
                </label>
                <label className="text-caption text-slate">
                  Bank AWS account ID
                  <input
                    className={inputClass}
                    value={account}
                    onChange={(e) => setAccount(e.target.value)}
                    inputMode="numeric"
                    placeholder="12 digits"
                    autoComplete="off"
                  />
                </label>
                <label className="text-caption text-slate">
                  AWS region
                  <input
                    className={inputClass}
                    value={region}
                    onChange={(e) => setRegion(e.target.value)}
                    placeholder="Key’s AWS region"
                    autoComplete="off"
                  />
                </label>
              </div>
              <p className="text-body text-slate">
                2. Add this statement to the key’s existing policy. Preserve its
                administrator statements. The aequorOS workload role also needs
                an IAM policy allowing these actions on the exact key ARN.
              </p>
              {role ? (
                <div className="space-y-2">
                  <CopyButton
                    text={statement}
                    label="bank key policy statement"
                  />
                  <pre className="max-w-full overflow-x-auto rounded bg-surface p-3 text-caption">
                    {statement}
                  </pre>
                </div>
              ) : (
                <p role="alert" className="text-body text-critical">
                  The aequorOS workload role has not been configured. Ask your
                  platform administrator to provide the aequorOS workload role
                  before applying the policy.
                </p>
              )}
              <p className="text-body text-slate">
                3. Test the connection. aequorOS wraps and unwraps a synthetic
                data key, then compares the result. Connect or rotate only after
                this succeeds.
              </p>
              {data?.connected && (
                <>
                  <p className="text-body text-slate">
                    Existing encrypted records and backups retain their previous
                    key versions. Keep previous keys enabled and
                    decrypt-capable.
                  </p>
                  <label className="block text-caption text-slate">
                    Reason for replacement-key rotation
                    <textarea
                      className={inputClass}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      minLength={10}
                      maxLength={2000}
                    />
                  </label>
                </>
              )}
              <div className="flex flex-wrap gap-3">
                <button
                  disabled={!valid}
                  className={buttonClass}
                  onClick={() =>
                    void perform(async () => {
                      setTested("");
                      const result = await apiCall(() =>
                        api.testBankKeyConnection({
                          bankId,
                          bankKeyConnect: payload,
                        }),
                      );
                      if (result.passed) {
                        setTested(identity);
                        setMessage(result.message);
                      } else setError(result.message);
                    })
                  }
                >
                  Test connection
                </button>
                <button
                  disabled={!canSave}
                  className={buttonClass}
                  onClick={() =>
                    void perform(async () => {
                      const result = await apiCall(() =>
                        data?.connected
                          ? api.rotateBankKey({
                              bankId,
                              bankKeyRotate: {
                                ...payload,
                                reason: reason.trim(),
                              },
                            })
                          : api.connectBankKey({
                              bankId,
                              bankKeyConnect: payload,
                            }),
                      );
                      cache.setQueryData(queryKey, result);
                      setTested("");
                      setReason("");
                      setKeyId("");
                      setRegion("");
                      setAccount("");
                      setConfirmed(false);
                      setMessage(
                        data?.connected
                          ? "Replacement key connected. Rotation recorded in the audit log."
                          : "Bank key connected. Setup recorded in the audit log.",
                      );
                    })
                  }
                >
                  {data?.connected ? "Rotate bank key" : "Connect bank key"}
                </button>
              </div>
            </fieldset>
            {connected && (
              <fieldset
                disabled={busy}
                className="min-w-0 space-y-3 border-t border-border-light pt-5"
              >
                <legend className="text-h3 text-navy">
                  Optional automatic AWS key-material rotation
                </legend>
                <p className="text-body text-slate">
                  A bank AWS administrator runs this command in the bank’s
                  account with kms:EnableKeyRotation permission on this key. AWS
                  does not allow aequorOS to enable rotation across accounts.
                  Native rotation keeps the same key ARN and is separate from
                  replacement-key rotation above.
                </p>
                <label className="block text-caption text-slate">
                  Rotation interval in days (90–2560)
                  <input
                    type="number"
                    min={90}
                    max={2560}
                    className={inputClass}
                    value={period}
                    onChange={(e) => {
                      setPeriod(Number(e.target.value));
                      setConfirmed(false);
                    }}
                  />
                </label>
                <CopyButton text={command} label="AWS rotation command" />
                <pre className="max-w-full overflow-x-auto rounded bg-surface p-3 text-caption">
                  {command}
                </pre>
                <label className="flex gap-2 text-body">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                  I ran this command successfully in the bank’s AWS account.
                </label>
                <button
                  className={buttonClass}
                  disabled={
                    !confirmed ||
                    !Number.isInteger(period) ||
                    period < 90 ||
                    period > 2560
                  }
                  onClick={() =>
                    void perform(async () => {
                      const result = await apiCall(() =>
                        api.confirmBankKeyRotationSchedule({
                          bankId,
                          bankKeyScheduleConfirm: {
                            keyId: connected.keyId,
                            periodDays: period,
                            reason:
                              "Bank administrator confirms native AWS rotation was enabled.",
                          },
                        }),
                      );
                      cache.setQueryData(queryKey, result);
                      setConfirmed(false);
                      setMessage(
                        "Bank-reported schedule confirmation recorded in the audit log.",
                      );
                    })
                  }
                >
                  Record schedule confirmation
                </button>
                <p className="text-caption text-slate">
                  {data?.rotationConfirmedAt
                    ? `Bank-reported schedule: every ${data.rotationPeriodDays} days, confirmed ${dateLabel(data.rotationConfirmedAt)}. Not independently verified by aequorOS.`
                    : "No bank-reported schedule. aequorOS does not independently verify AWS scheduling."}
                </p>
              </fieldset>
            )}
          </>
        )}
        <div aria-live="polite">
          {busy && <p>Checking key configuration…</p>}
          {message && (
            <p role="status" className="text-body text-success">
              {message}
            </p>
          )}
          {error && (
            <p role="alert" className="text-body text-critical">
              {error}
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
