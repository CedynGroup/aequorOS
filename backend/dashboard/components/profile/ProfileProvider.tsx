'use client';

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import type {
  MeResponse,
  ProfileUpdateRequest,
} from '@aequoros/risk-service-api';

import { apiCall, authApi } from '@/lib/api/client';

type ProfileContextValue = {
  profile: MeResponse | undefined;
  isLoading: boolean;
  error: Error | null;
  updateProfile: (updates: ProfileUpdateRequest) => Promise<MeResponse>;
  isSaving: boolean;
  refetch: () => Promise<MeResponse | undefined>;
};

const ProfileContext = createContext<ProfileContextValue | null>(null);

export function useUserProfile(): ProfileContextValue {
  const value = useContext(ProfileContext);
  if (!value) {
    throw new Error('useUserProfile must be used within <ProfileProvider>.');
  }
  return value;
}

export default function ProfileProvider({ children }: { children: ReactNode }) {
  const { data: session, status } = useSession();
  const queryClient = useQueryClient();
  const updateQueue = useRef<Promise<void>>(Promise.resolve());
  const profileQueryKey = useMemo(
    () => [
      'auth',
      'me',
      session?.user?.organizationId ?? null,
      session?.user?.email ?? null,
    ],
    [session?.user?.email, session?.user?.organizationId],
  );
  const profileQuery = useQuery({
    queryKey: profileQueryKey,
    queryFn: () => apiCall(() => authApi.authMe()),
    // Both conditions, not just the status. NextAuth reports 'authenticated'
    // from the session COOKIE, which outlives the backend access token it
    // carries — and on a stale session TokenSync sets that token to null (or a
    // failed silent refresh leaves session.error set). Gating on status alone
    // fires authMe() with no bearer, which the API correctly answers 401 and
    // which surfaces as a red console error on the sign-in page while the
    // sign-out redirect is still in flight. There is nothing to ask the API
    // until we hold a token to ask it with.
    enabled: status === 'authenticated' && Boolean(session?.accessToken) && !session?.error,
    staleTime: 5 * 60_000,
  });
  const updateMutation = useMutation({
    onMutate: () =>
      queryClient.cancelQueries({ queryKey: profileQueryKey, exact: true }),
    mutationFn: (updates: ProfileUpdateRequest) =>
      apiCall(() => authApi.authUpdateMe({ profileUpdateRequest: updates })),
    onSuccess: (profile) => {
      queryClient.setQueryData(profileQueryKey, profile);
    },
  });
  const { mutateAsync, isPending } = updateMutation;
  const { refetch: refetchProfile } = profileQuery;
  const updateProfile = useCallback(
    (updates: ProfileUpdateRequest) => {
      const request = updateQueue.current.then(() => mutateAsync(updates));
      updateQueue.current = request.then(
        () => undefined,
        () => undefined,
      );
      return request;
    },
    [mutateAsync],
  );
  const refetch = useCallback(
    async () => (await refetchProfile()).data,
    [refetchProfile],
  );

  const value = useMemo<ProfileContextValue>(
    () => ({
      profile: profileQuery.data,
      isLoading: profileQuery.isLoading,
      error: profileQuery.error,
      updateProfile,
      isSaving: isPending,
      refetch,
    }),
    [
      profileQuery.data,
      profileQuery.error,
      profileQuery.isLoading,
      refetch,
      isPending,
      updateProfile,
    ],
  );

  return (
    <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>
  );
}
