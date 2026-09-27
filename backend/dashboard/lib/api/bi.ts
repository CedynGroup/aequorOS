"use client";

/**
 * The dashboard's client for the six BI read routes.
 *
 * `GET …/bi/catalogue` says what this principal may ask; `POST …/bi/query`
 * answers one question; `…/bi/grid` and `…/bi/drill` page an answer;
 * `…/bi/explain` says where a figure came from; `GET …/bi/trust` says whether
 * the figures reconcile to what the platform already files. Nothing is decided
 * here: every route re-evaluates the caller's bindings against every member the
 * submitted query touches, so the browser's job is to ask honestly, to key the
 * answer so it cannot be reused by anyone else, and to render a refusal as a
 * refusal.
 *
 * Cache identity lives in `./biKeys`, which is pure and separately proved. The
 * one rule to keep in mind when adding a hook here: a BI cache key must carry
 * the tenant, the actor, the authorization generation, the institution, the
 * reporting window and the query — see that module for why.
 *
 * `BI_ENABLED` defaults off in every deployment, and with the flag off each
 * route answers 404 exactly as an unmounted path would. `useBiAvailability`
 * asks `GET /feature-flags` so navigation can decline to offer a door that is
 * walled up, and `isBiUnavailable` recognises the 404 for a surface that is
 * opened anyway.
 */

import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BiApi,
  FeatureFlagsApi,
  type BiCatalogueRead,
  type BiExplainRead,
  type BiGridPageRead,
  type BiPagedQueryRequest,
  type BiQuery,
  type BiQueryResult,
  type BiTrustRead,
} from "@aequoros/risk-service-api";
import { apiCall, configuration, isApiError } from "./client";
import { useQueryAuthorityScope } from "./useQueryScope";
import {
  biCatalogueKey,
  biDrillKey,
  biExplainKey,
  biFeatureKey,
  biGridKey,
  biQueryKey,
  biTrustKey,
  isoDay,
  utcDay,
} from "./biKeys";

const biApi = new BiApi(configuration);
const featureFlagsApi = new FeatureFlagsApi(configuration);

/**
 * The server refused at least one member of the submitted query.
 *
 * There is no partial answer: one denied member denies the whole query, and the
 * response carries no rows. The 403 body names the members, and the UI
 * deliberately does not show them — see `components/bi/RestrictedWidget.tsx`.
 */
export function isBiAccessDenied(error: unknown): boolean {
  if (!isApiError(error)) return false;
  return error.status === 403;
}

/**
 * BI is not served here. Either the deployment has the feature off, or the
 * institution belongs to another tenant — both answer 404, which is the point:
 * a feature that is off is not "forbidden", it is not there.
 */
export function isBiUnavailable(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

/**
 * Whether this deployment serves BI at all.
 *
 * `undefined` means "not yet known" and is distinct from `false`: navigation
 * hides BI until the answer arrives, so no link flashes, while the route guard
 * only refuses once the answer is a definite no.
 */
export function useBiAvailability(enabled = true): {
  biEnabled: boolean | undefined;
  isLoading: boolean;
} {
  const scope = useQueryAuthorityScope();
  const query = useQuery({
    queryKey: biFeatureKey(scope),
    queryFn: () => apiCall(() => featureFlagsApi.readFeatureFlags()),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return {
    // A failed flag read is not an entitlement. Fail closed.
    biEnabled: query.isError ? false : query.data?.biEnabled,
    isLoading: query.isPending,
  };
}

/** Every measure, dimension and drill path THIS caller may query. */
export function useBiCatalogue(bankId: string | undefined, enabled = true) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiCatalogueRead>({
    queryKey: biCatalogueKey(scope, bankId),
    queryFn: () => apiCall(() => biApi.getBiCatalogue({ bankId: bankId! })),
    enabled: enabled && Boolean(bankId),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** One answered question, under the interactive row cap. */
export function useBiQuery(
  bankId: string | undefined,
  query: BiQuery | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiQueryResult>({
    queryKey: biQueryKey(scope, bankId, query),
    queryFn: () =>
      apiCall(() => biApi.runBiQuery({ bankId: bankId!, biQuery: query! })),
    enabled: enabled && Boolean(bankId) && query !== null,
    retry: false,
  });
}

/** One page of grouped, pivoted or plain rows. */
export function useBiGrid(
  bankId: string | undefined,
  request: BiPagedQueryRequest | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiGridPageRead>({
    queryKey: biGridKey(scope, bankId, request),
    queryFn: () =>
      apiCall(() =>
        biApi.runBiGridQuery({
          bankId: bankId!,
          biPagedQueryRequest: request!,
        }),
      ),
    enabled: enabled && Boolean(bankId) && request !== null,
    retry: false,
  });
}

/**
 * Fetch one grid page on demand, through the same cache identity `useBiGrid`
 * uses.
 *
 * AG Grid's Infinite Row Model asks for a block of rows from a callback, which
 * is not a place a hook can be called — so paging needs an imperative fetch.
 * It goes through `QueryClient.fetchQuery` rather than straight to the API so
 * that a page already in the cache is not asked for twice, and — the reason
 * this lives here and not in a component — so the key is built by `biGridKey`
 * like every other BI read. A page fetched under a hand-rolled key would be
 * cached without the tenant, the actor or the authorization generation in it,
 * which is exactly the leak `./biKeys` exists to prevent.
 *
 * Omitting `endRow` asks the server for one page at ITS cap, which is how the
 * grid learns the page size instead of choosing one.
 */
export function useBiGridPages(
  bankId: string | undefined,
): (request: BiPagedQueryRequest) => Promise<BiGridPageRead> {
  const scope = useQueryAuthorityScope();
  const client = useQueryClient();
  return useCallback(
    (request: BiPagedQueryRequest) =>
      client.fetchQuery<BiGridPageRead>({
        queryKey: biGridKey(scope, bankId, request),
        queryFn: () =>
          apiCall(() =>
            biApi.runBiGridQuery({
              bankId: bankId!,
              biPagedQueryRequest: request,
            }),
          ),
        retry: false,
      }),
    [bankId, client, scope],
  );
}

/** The records behind a figure: one capped page at the record grain. */
export function useBiDrill(
  bankId: string | undefined,
  request: BiPagedQueryRequest | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiGridPageRead>({
    queryKey: biDrillKey(scope, bankId, request),
    queryFn: () =>
      apiCall(() =>
        biApi.runBiDrillQuery({
          bankId: bankId!,
          biPagedQueryRequest: request!,
        }),
      ),
    enabled: enabled && Boolean(bankId) && request !== null,
    retry: false,
  });
}

/** Where one figure of one query came from — definition, source, checks. */
export function useBiExplain(
  bankId: string | undefined,
  measure: string | null,
  query: BiQuery | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiExplainRead>({
    queryKey: biExplainKey(scope, bankId, measure ?? "unset", query),
    queryFn: () =>
      apiCall(() =>
        biApi.explainBiMeasure({
          bankId: bankId!,
          biExplainRequest: { query: query!, measure: measure! },
        }),
      ),
    enabled: enabled && Boolean(bankId) && measure !== null && query !== null,
    retry: false,
  });
}

/** Every reconciliation check for one (institution, date), with its evidence. */
export function useBiTrust(
  bankId: string | undefined,
  asOf: string | null | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  const day = isoDay(asOf);
  return useQuery<BiTrustRead>({
    queryKey: biTrustKey(scope, bankId, day),
    queryFn: () =>
      apiCall(() => biApi.getBiTrust({ bankId: bankId!, asOf: utcDay(day!) })),
    enabled: enabled && Boolean(bankId) && day !== null,
    retry: false,
  });
}

export {
  biCatalogueKey,
  biDrillKey,
  biExplainKey,
  biGridKey,
  biQueryKey,
  biTrustKey,
  isoDay,
  utcDay,
} from "./biKeys";
