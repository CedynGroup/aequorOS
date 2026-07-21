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
    enabled: status === 'authenticated',
    staleTime: 5 * 60_000,
  });
  const updateMutation = useMutation({
    mutationFn: (updates: ProfileUpdateRequest) =>
      apiCall(() => authApi.authUpdateMe({ profileUpdateRequest: updates })),
    onSuccess: (profile) => {
      queryClient.setQueryData(profileQueryKey, profile);
    },
  });
  const updateProfile = useCallback(
    (updates: ProfileUpdateRequest) => {
      const request = updateQueue.current.then(() =>
        updateMutation.mutateAsync(updates),
      );
      updateQueue.current = request.then(
        () => undefined,
        () => undefined,
      );
      return request;
    },
    [updateMutation.mutateAsync],
  );
  const refetch = useCallback(
    async () => (await profileQuery.refetch()).data,
    [profileQuery.refetch],
  );

  const value = useMemo<ProfileContextValue>(
    () => ({
      profile: profileQuery.data,
      isLoading: profileQuery.isLoading,
      error: profileQuery.error,
      updateProfile,
      isSaving: updateMutation.isPending,
      refetch,
    }),
    [
      profileQuery.data,
      profileQuery.error,
      profileQuery.isLoading,
      refetch,
      updateMutation.isPending,
      updateProfile,
    ],
  );

  return (
    <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>
  );
}
