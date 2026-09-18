"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import PageHeader from "@/components/ui/PageHeader";
import { Card, CardBody } from "@/components/ui/Card";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import {
  GrantReasonFields,
  type GrantReasonDraft,
} from "@/components/access/GrantReasonFields";
import { authorizationApi, normalizeApiError } from "@/lib/api/client";
import type { AccessDeniedRoute } from "@/lib/modules";

const initialReason: GrantReasonDraft = {
  reasonCategory: "role_change",
  reasonDetail: "",
  reference: "",
  validUntil: "",
};

export default function AccessDeniedPage({
  denied,
  route,
}: {
  denied: AccessDeniedRoute;
  route: string;
}) {
  const queryClient = useQueryClient();
  const institutions = useQuery({
    queryKey: ["access", "request-institutions"],
    queryFn: () => authorizationApi.listAccessRequestInstitutions(),
  });
  const requests = useQuery({
    queryKey: ["access", "my-requests"],
    queryFn: () => authorizationApi.listMyAuthorizationAccessRequests(),
  });
  const [institutionId, setInstitutionId] = useState("");
  const [reason, setReason] = useState(initialReason);
  const [formOpen, setFormOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const targetInstitution =
    institutionId || institutions.data?.institutions[0]?.id || "";
  const pending = useMemo(
    () =>
      requests.data?.requests.filter(
        (request) =>
          request.status === "pending" &&
          request.route === route &&
          request.institutionId === targetInstitution &&
          denied.requirements.some(
            (required) =>
              request.moduleScope === required.moduleScope &&
              request.sensitivityScope === required.sensitivityScope &&
              request.permission === required.permission,
          ),
      ) ?? [],
    [denied.requirements, requests.data?.requests, route, targetInstitution],
  );
  const allRequirementsPending = denied.requirements.every((required) =>
    pending.some(
      (request) =>
        request.moduleScope === required.moduleScope &&
        request.sensitivityScope === required.sensitivityScope &&
        request.permission === required.permission,
    ),
  );
  const create = useMutation({
    mutationFn: async () =>
      Promise.all(
        denied.requirements.map((required) =>
          authorizationApi.createAuthorizationAccessRequest({
            accessRequestCreate: {
              route,
              institutionId: targetInstitution,
              moduleScope: required.moduleScope,
              sensitivityScope: required.sensitivityScope,
              permission: required.permission,
              reasonCategory: reason.reasonCategory,
              reasonDetail: reason.reasonDetail.trim(),
              reference: reason.reference.trim() || undefined,
            },
          }),
        ),
      ),
    onSuccess: () => {
      setFormOpen(false);
      void queryClient.invalidateQueries({
        queryKey: ["access", "my-requests"],
      });
    },
    onError: async (failure) =>
      setError((await normalizeApiError(failure)).message),
  });

  const complete =
    Boolean(targetInstitution) &&
    (reason.reasonCategory !== "other" || Boolean(reason.reasonDetail.trim()));

  return (
    <>
      <PageHeader title={denied.title} />
      <div className="mx-auto max-w-3xl px-4 py-10 md:px-8">
        <Card>
          <CardBody className="space-y-5 p-7">
            <div className="flex items-start gap-4">
              <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-warning-light text-warning">
                <LockKeyhole size={20} aria-hidden />
              </span>
              <div>
                <h2 className="text-h2 text-navy">Access required</h2>
                <p className="mt-1 text-body text-slate">
                  You can see that this page exists, but your current access
                  does not include it.
                </p>
              </div>
            </div>
            <DisabledWithReason reason={denied.reason}>
              <div className="rounded-md border border-border-light bg-surface p-4">
                <p className="text-caption font-medium text-navy">
                  Required permission{denied.requirements.length > 1 ? "s" : ""}
                </p>
                <ul className="mt-2 space-y-1 text-body text-navy">
                  {denied.requirements.map((required) => (
                    <li key={`${required.moduleScope}-${required.sensitivityScope}`}>
                      {required.moduleLabel} · {required.sensitivityLabel} ·{" "}
                      {required.permissionLabel}
                    </li>
                  ))}
                </ul>
              </div>
            </DisabledWithReason>
            <p className="text-body text-slate">
              An organization owner or admin can grant this access.
            </p>
            {allRequirementsPending ? (
              <p role="status" className="rounded-md bg-action-light px-4 py-3 text-body text-navy">
                Requested on{" "}
                {pending[0].requestedAt.toLocaleDateString("en-GB", {
                  day: "numeric",
                  month: "short",
                })}{" "}
                · waiting for an organization owner
              </p>
            ) : formOpen ? (
              <form
                className="space-y-4 rounded-md border border-border-light p-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (complete) create.mutate();
                }}
              >
                {institutions.data && institutions.data.institutions.length > 1 && (
                  <label className="block">
                    <span className="mb-1.5 block text-caption font-medium text-navy">
                      Institution
                    </span>
                    <select
                      value={targetInstitution}
                      onChange={(event) => setInstitutionId(event.target.value)}
                      className="w-full rounded-md border border-border bg-surface px-3 py-2.5 text-body text-navy"
                    >
                      {institutions.data.institutions.map((institution) => (
                        <option key={institution.id} value={institution.id}>
                          {institution.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <GrantReasonFields
                  value={reason}
                  onChange={setReason}
                  includeExpiry={false}
                />
                {error && <p role="alert" className="text-caption text-danger">{error}</p>}
                <div className="flex justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setFormOpen(false)}
                    className="rounded-md border border-border px-4 py-2.5 text-body font-medium text-navy"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={!complete || create.isPending}
                    className="btn-primary px-4 py-2.5 text-body font-medium disabled:opacity-50"
                  >
                    {create.isPending ? "Requesting…" : "Submit request"}
                  </button>
                </div>
              </form>
            ) : (
              <button
                type="button"
                disabled={institutions.isLoading || !targetInstitution}
                onClick={() => setFormOpen(true)}
                className="btn-primary w-fit px-4 py-2.5 text-body font-medium disabled:opacity-50"
              >
                Request access
              </button>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
