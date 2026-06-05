import { Children, type ReactNode } from "react";

import { Alert, Skeleton } from "../components/ui";
import { isApiError } from "../lib/api";

export function DataList({
  loading,
  error,
  empty,
  children,
}: {
  loading: boolean;
  error: unknown;
  empty: string;
  children: ReactNode;
}) {
  const content = Children.toArray(children).filter(Boolean);
  if (loading) return <Skeleton className="h-52" />;
  if (error) return <ErrorPanel error={error} />;
  return <div className="space-y-2">{content.length > 0 ? content : <EmptyRow label={empty} />}</div>;
}

export function EmptyRow({ label }: { label: string }) {
  return <div className="px-3 py-2 text-xs text-[rgb(var(--muted-foreground))]">{label}</div>;
}

export function ErrorPanel({ error }: { error: unknown }) {
  if (isApiError(error)) {
    return (
      <Alert title={`${error.statusCode} ${error.code}`} tone={error.statusCode === 409 ? "warning" : "danger"}>
        {error.message}
      </Alert>
    );
  }
  return <Alert title="Request failed" tone="danger">{error instanceof Error ? error.message : "Unknown error"}</Alert>;
}
