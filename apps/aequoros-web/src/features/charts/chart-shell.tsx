import { Component, Suspense, type ErrorInfo, type ReactNode } from "react";

import { Alert, Skeleton } from "../../components/ui";

export function ChartBoundary({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <ChartErrorBoundary title={title}>
      <Suspense fallback={<ChartLoading title={title} />}>{children}</Suspense>
    </ChartErrorBoundary>
  );
}

function ChartLoading({ title }: { title: string }) {
  return (
    <div aria-label={`Loading ${title}`} className="space-y-2 py-2">
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

class ChartErrorBoundary extends Component<
  { title: string; children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // The explicit state below keeps a failed optional visualization from
    // replacing its authoritative table.
  }

  render() {
    if (this.state.error) {
      return (
        <Alert title={`${this.props.title} unavailable`} tone="warning">
          The chart could not be displayed. The tabular values remain available
          below.
        </Alert>
      );
    }
    return this.props.children;
  }
}
