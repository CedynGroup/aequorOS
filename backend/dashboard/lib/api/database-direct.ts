'use client';

/**
 * Data Engine — Database (Direct) API layer: the generated DatabaseDirectApi
 * client plus TanStack Query hooks for the read-only core-database adapter.
 *
 * Onboard one direct connection to a bank-hosted reporting replica (Oracle,
 * SQL Server, or a generic JDBC/ODBC endpoint), rotate its credentials, test
 * reachability, discover the source schema for mapping, and run an on-demand
 * sync. Credentials are WRITE-ONLY: every payload sends them once and the API
 * only ever returns status, fingerprint, and expiry.
 *
 * Kept separate from lib/api/client.ts and hooks.ts so Data Engine work does
 * not contend with the regulatory modules' files.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  DatabaseDirectApi,
  type DatabaseConnectionCreate,
  type DatabaseConnectionSyncRequest,
  type DatabaseConnectionUpdate,
} from '@aequoros/risk-service-api';
import { apiCall, configuration } from './client';

// Reuse the shared, token-bearing Configuration so direct-connection calls
// authenticate with the same backend JWT as every other module.
export const databaseDirectApi = new DatabaseDirectApi(configuration);


/** Query keys touched by every connection mutation, invalidated on success. */
const invalidatePrefixes = ['db-direct-connections'];

export function useDatabaseConnections(bankId: string | undefined) {
  return useQuery({
    queryKey: ['db-direct-connections', bankId],
    queryFn: () =>
      apiCall(() =>
        databaseDirectApi.listDatabaseDirectConnections({ bankId: bankId! }),
      ),
    enabled: Boolean(bankId),
  });
}

export function useDatabaseConnection(
  bankId: string | undefined,
  connectionId: string | undefined,
) {
  return useQuery({
    queryKey: ['db-direct-connection', bankId, connectionId],
    queryFn: () =>
      apiCall(() =>
        databaseDirectApi.getDatabaseDirectConnection({
          bankId: bankId!,
          connectionId: connectionId!,
        }),
      ),
    enabled: Boolean(bankId && connectionId),
  });
}

export function useCreateDatabaseConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DatabaseConnectionCreate) =>
      apiCall(() =>
        databaseDirectApi.createDatabaseDirectConnection({
          bankId: bankId!,
          databaseConnectionCreate: payload,
        }),
      ),
    onSuccess: () => {
      invalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * Config edits and credential rotation. When credentials are present the new
 * set is validated FIRST; only on success is the stored ciphertext swapped in
 * one transaction. On failure nothing changes and a 422 is returned.
 */
export function useUpdateDatabaseConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      connectionId,
      payload,
    }: {
      connectionId: string;
      payload: DatabaseConnectionUpdate;
    }) =>
      apiCall(() =>
        databaseDirectApi.updateDatabaseDirectConnection({
          bankId: bankId!,
          connectionId,
          databaseConnectionUpdate: payload,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['db-direct-connections'] });
    },
  });
}

/** Live reachability probe. Reports latency + row/table counts on success, or a
 * pre-authored, bank-safe classified error — never a raw driver exception. */
export function useTestDatabaseConnection(bankId: string | undefined) {
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        databaseDirectApi.testDatabaseDirectConnection({
          bankId: bankId!,
          connectionId,
        }),
      ),
  });
}

/** Live introspection pull: source tables with their columns and a few sample
 * values per column, to inform per-institution mapping. */
export function useDiscoverDatabaseSchema(bankId: string | undefined) {
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        databaseDirectApi.discoverDatabaseDirectSchema({
          bankId: bankId!,
          connectionId,
        }),
      ),
  });
}

/** On-demand sync for one as-of date (defaults to today). Extracts through the
 * adapter, runs the ETL preprocess + dedup pass, validates, and persists an
 * immutable ingestion batch. Returns the batch id + terminal status. */
export function useSyncDatabaseConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      connectionId,
      asOfDate,
      reason,
    }: {
      connectionId: string;
      asOfDate?: string;
      reason?: string;
    }) => {
      const request: DatabaseConnectionSyncRequest = {
        // The generated client types this nullable date as an ISO string.
        asOfDate: asOfDate ? `${asOfDate}T00:00:00Z` : undefined,
        reason,
      };
      return apiCall(() =>
        databaseDirectApi.syncDatabaseDirectConnection({
          bankId: bankId!,
          connectionId,
          databaseConnectionSyncRequest: request,
        }),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['db-direct-connections'] });
      // A sync mints a DB_DIRECT batch and may add canonical positions.
      void queryClient.invalidateQueries({ queryKey: ['de-batches', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-positions', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-summary', bankId] });
    },
  });
}

export function useDisableDatabaseConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        databaseDirectApi.disableDatabaseDirectConnection({
          bankId: bankId!,
          connectionId,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['db-direct-connections'] });
    },
  });
}

export function useEnableDatabaseConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        databaseDirectApi.enableDatabaseDirectConnection({
          bankId: bankId!,
          connectionId,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['db-direct-connections'] });
    },
  });
}

export function useRevokeDatabaseConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        databaseDirectApi.revokeDatabaseDirectConnection({
          bankId: bankId!,
          connectionId,
        }),
      ),
    onSuccess: () => {
      invalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}
