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
  const { profile, updateProfile } = useUserProfile();
  const [theme, setThemeState] = useState<ThemePreference>('system');
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>('dark');

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
    setThemeState(local);
    setResolvedTheme(readDocumentTheme());
  }, []);

  useEffect(() => {
    if (profile?.theme) applyTheme(profile.theme);
  }, [applyTheme, profile?.theme]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => {
      if (theme === 'system') applyTheme('system');
    };
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [applyTheme, theme]);

  const setTheme = useCallback(
    (next: ThemePreference) => {
      applyTheme(next);
      void updateProfile({ theme: next }).catch(() => {
        // Keep the immediate local preference if persistence is temporarily
        // unavailable; the next successful profile load remains authoritative.
      });
    },
    [applyTheme, updateProfile],
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
