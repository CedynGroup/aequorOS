'use client';

/**
 * Theme provider for the dark-first token system.
 *
 * The inline script in app/layout.tsx applies the last local preference before
 * paint. Once authenticated, the user profile becomes the source of truth and
 * this provider mirrors changes back through PATCH /auth/me.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useUserProfile } from '@/components/profile/ProfileProvider';

export type ThemePreference = 'dark' | 'light' | 'system';
export type ResolvedTheme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'aeq-theme';

type ThemeContextValue = {
  theme: ThemePreference;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: ThemePreference) => void;
  toggle: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) {
    throw new Error('useTheme must be used within <ThemeProvider>.');
  }
  return value;
}

function readDocumentTheme(): ResolvedTheme {
  if (typeof document === 'undefined') return 'dark';
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== 'system') return preference;
  return window.matchMedia('(prefers-color-scheme: light)').matches
    ? 'light'
    : 'dark';
}

function readLocalPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'dark' || stored === 'light' || stored === 'system') {
      return stored;
    }
  } catch {
    // Storage unavailable; use the OS preference.
  }
  return 'system';
}

export default function ThemeProvider({ children }: { children: ReactNode }) {
  const { profile, updateProfile, refetch } = useUserProfile();
  const [theme, setThemeState] = useState<ThemePreference>('system');
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>('dark');
  const pendingTheme = useRef<ThemePreference | null>(null);
  const isSyncing = useRef(false);
  const confirmedTheme = useRef<ThemePreference>('system');

  const applyTheme = useCallback((preference: ThemePreference) => {
    const resolved = resolveTheme(preference);
    setThemeState(preference);
    setResolvedTheme(resolved);
    document.documentElement.dataset.theme = resolved;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    } catch {
      // Storage unavailable (private mode) — theme still applies this session.
    }
  }, []);

  useEffect(() => {
    const local = readLocalPreference();
    confirmedTheme.current = local;
    setThemeState(local);
    setResolvedTheme(readDocumentTheme());
  }, []);

  useEffect(() => {
    if (profile?.theme && !isSyncing.current) {
      confirmedTheme.current = profile.theme;
      applyTheme(profile.theme);
    }
  }, [applyTheme, profile?.theme]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => {
      if (theme === 'system') applyTheme('system');
    };
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [applyTheme, theme]);

  const flushTheme = useCallback(async () => {
    if (isSyncing.current) return;
    isSyncing.current = true;

    try {
      while (pendingTheme.current) {
        const next = pendingTheme.current;
        pendingTheme.current = null;
        try {
          const savedProfile = await updateProfile({ theme: next });
          if (!pendingTheme.current) {
            const savedTheme = savedProfile.theme ?? next;
            confirmedTheme.current = savedTheme;
            applyTheme(savedTheme);
          }
        } catch {
          const refreshedProfile = await refetch().catch(() => undefined);
          if (!pendingTheme.current) {
            const canonicalTheme =
              refreshedProfile?.theme ?? confirmedTheme.current;
            confirmedTheme.current = canonicalTheme;
            applyTheme(canonicalTheme);
          }
        }
      }
    } finally {
      isSyncing.current = false;
    }
  }, [applyTheme, refetch, updateProfile]);

  const setTheme = useCallback(
    (next: ThemePreference) => {
      applyTheme(next);
      pendingTheme.current = next;
      void flushTheme();
    },
    [applyTheme, flushTheme],
  );

  const toggle = useCallback(() => {
    setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');
  }, [resolvedTheme, setTheme]);

  const value = useMemo(
    () => ({ theme, resolvedTheme, setTheme, toggle }),
    [theme, resolvedTheme, setTheme, toggle],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
