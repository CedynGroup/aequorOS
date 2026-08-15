'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, toApiError } from './api';
import { useToast } from '@/components/ui/Toast';

/**
 * Minimal fetch-on-mount hook: loading → data | error, with a reload()
 * that re-runs the fetcher (used by every ApiError panel's Retry button).
 *
 * `deps` re-runs the fetch when route params change (e.g. orgId).
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetcherRef
      .current()
      .then((d) => {
        if (!alive) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(toApiError(e));
        setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  return { data, error, loading, reload };
}

/**
 * Standardized mutation hook: `{ mutate, loading, error, data, reset }` around
 * an async API call, with built-in success/failure toasts — so feature pages
 * stop juggling manual busy/error/reload state. Not react-query; deliberately
 * minimal.
 *
 * `mutate` resolves to the result on success or `undefined` on failure (the
 * error is captured in `error` and surfaced as a toast). Chain a reload in
 * `onSuccess`.
 */
export function useMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
  opts: {
    onSuccess?: (result: TResult, ...args: TArgs) => void;
    onError?: (error: ApiError, ...args: TArgs) => void;
    /** Toast title on success (string or derived from the result). Omit for none. */
    successMessage?: string | ((result: TResult) => string);
    /** Prefixes the failure toast/title, e.g. "Publish". */
    errorContext?: string;
    /** Set false to suppress the automatic toasts. Default true. */
    toast?: boolean;
  } = {},
) {
  const [data, setData] = useState<TResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const toaster = useToast();

  const optsRef = useRef(opts);
  optsRef.current = opts;
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const mutate = useCallback(
    async (...args: TArgs): Promise<TResult | undefined> => {
      const o = optsRef.current;
      const withToast = o.toast !== false;
      setLoading(true);
      setError(null);
      try {
        const result = await fnRef.current(...args);
        setData(result);
        setLoading(false);
        if (withToast && o.successMessage) {
          const msg =
            typeof o.successMessage === 'function' ? o.successMessage(result) : o.successMessage;
          toaster.success(msg);
        }
        o.onSuccess?.(result, ...args);
        return result;
      } catch (e) {
        const apiError = toApiError(e);
        setError(apiError);
        setLoading(false);
        if (withToast) {
          toaster.error(o.errorContext ? `${o.errorContext} failed` : 'Request failed', {
            description: `${apiError.code} · ${apiError.message}`,
          });
        }
        o.onError?.(apiError, ...args);
        return undefined;
      }
    },
    [toaster],
  );

  const reset = useCallback(() => {
    setData(null);
    setError(null);
    setLoading(false);
  }, []);

  return { mutate, loading, error, data, reset };
}
