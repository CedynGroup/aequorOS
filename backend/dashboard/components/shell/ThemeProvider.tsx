'use client';

/**
 * Theme provider for the dark-first token system.
 *
 * The actual `data-theme` attribute is set before paint by an inline script
 * in app/layout.tsx (reads localStorage('aeq-theme'), defaults to 'dark') so
 * there is no flash of the wrong theme. This provider mirrors that value into
 * React state and exposes `useTheme()` for the header toggle.
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

export type Theme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'aeq-theme';

type ThemeContextValue = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
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

function readDocumentTheme(): Theme {
  if (typeof document === 'undefined') return 'dark';
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

export default function ThemeProvider({ children }: { children: ReactNode }) {
  // SSR renders 'dark' (the default); the inline script has already stamped
  // the real value on <html> before hydration, so sync it after mount.
  const [theme, setThemeState] = useState<Theme>('dark');

  useEffect(() => {
    setThemeState(readDocumentTheme());
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage unavailable (private mode) — theme still applies this session.
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(readDocumentTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  const value = useMemo(
    () => ({ theme, setTheme, toggle }),
    [theme, setTheme, toggle]
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
