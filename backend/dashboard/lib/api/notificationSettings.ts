/**
 * Notification settings: a person's own email choices, the organisation's
 * policy (account administrators) and the delivery log.
 *
 * Query keys: ["notification-preferences", …scope], ["notification-settings",
 * …scope], ["notification-deliveries", …scope, status, offset].
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  DeliveryStatus,
  EmailMode,
  NotificationDeliveryListRead,
  NotificationOrgSettingsRead,
  NotificationOrgSettingsUpdate,
  NotificationPreferencesRead,
  PoliciesValue,
} from "@aequoros/risk-service-api";

import { apiCall, isApiError, notificationsApi } from "./client";
import { scopedQueryKey } from "./queryPolicy";
import { useQueryAuthorityScope } from "./useQueryScope";

export function useNotificationPreferences() {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("notification-preferences", scope),
    queryFn: () => apiCall(() => notificationsApi.getNotificationPreferences()),
  });
}

/** Save one event's choice; ``null`` returns it to the organisation default. */
export function useSetNotificationPreference() {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  const queryKey = scopedQueryKey("notification-preferences", scope);
  return useMutation({
    mutationFn: ({ key, mode }: { key: string; mode: EmailMode | null }) =>
      apiCall(() =>
        notificationsApi.updateNotificationPreferences({
          notificationPreferencesUpdate: { choices: { [key]: mode } },
        }),
      ),
    onMutate: async ({ key, mode }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<NotificationPreferencesRead>(queryKey);
      if (previous) {
        queryClient.setQueryData<NotificationPreferencesRead>(queryKey, {
          ...previous,
          categories: previous.categories.map((category) => ({
            ...category,
            events: category.events.map((event) =>
              event.key === key
                ? {
                    ...event,
                    choice: mode,
                    emailMode: mode ?? event.defaultEmail,
                  }
                : event,
            ),
          })),
        });
      }
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKey, saved);
    },
  });
}

/**
 * The organisation's settings. A 403 means the person is not an account
 * administrator: the query resolves to ``null`` (not an error) so callers can
 * simply hide the administrator links.
 */
export function useNotificationOrgSettings() {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("notification-settings", scope),
    queryFn: async (): Promise<NotificationOrgSettingsRead | null> => {
      try {
        return await apiCall(() => notificationsApi.getNotificationSettings());
      } catch (error) {
        if (isApiError(error) && error.status === 403) return null;
        throw error;
      }
    },
    retry: false,
  });
}

export type PolicyEdit = { emailMode: EmailMode; locked: boolean } | null;

export function useUpdateNotificationOrgSettings() {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      update: Omit<NotificationOrgSettingsUpdate, "policies"> & {
        policies: Record<string, PolicyEdit>;
      },
    ) =>
      apiCall(() =>
        notificationsApi.updateNotificationSettings({
          notificationOrgSettingsUpdate: {
            ...update,
            // ``null`` removes a rule; the generated type omits the null the
            // API accepts, and its serializer passes null through unchanged.
            policies: update.policies as Record<string, PoliciesValue>,
          },
        }),
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData(scopedQueryKey("notification-settings", scope), saved);
      void queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });
    },
  });
}

export const DELIVERY_PAGE_SIZE = 25;

export function useNotificationDeliveries(status: DeliveryStatus | null, offset: number) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey("notification-deliveries", scope, status, offset),
    queryFn: (): Promise<NotificationDeliveryListRead> =>
      apiCall(() =>
        notificationsApi.listNotificationDeliveries({
          status,
          limit: DELIVERY_PAGE_SIZE,
          offset,
        }),
      ),
    placeholderData: (previous) => previous,
    refetchInterval: 30_000,
  });
}

export function useRetryNotificationDelivery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (deliveryId: string) =>
      apiCall(() => notificationsApi.retryNotificationDelivery({ deliveryId })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notification-deliveries"] });
    },
  });
}
