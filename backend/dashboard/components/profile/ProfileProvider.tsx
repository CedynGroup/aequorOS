'use client';

import { createContext, useContext, useMemo, type ReactNode } from 'react';
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
  refetch: () => Promise<unknown>;
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

  const value = useMemo<ProfileContextValue>(
    () => ({
      profile: profileQuery.data,
      isLoading: profileQuery.isLoading,
      error: profileQuery.error,
      updateProfile: updateMutation.mutateAsync,
      isSaving: updateMutation.isPending,
      refetch: profileQuery.refetch,
    }),
    [
      profileQuery.data,
      profileQuery.error,
      profileQuery.isLoading,
      profileQuery.refetch,
      updateMutation.isPending,
      updateMutation.mutateAsync,
    ],
  );

  return (
    <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>
  );
}
