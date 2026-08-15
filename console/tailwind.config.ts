import type { Config } from 'tailwindcss';

/**
 * DELIBERATE DUPLICATION of backend/dashboard/tailwind.config.ts (color
 * mapping + typography scale) so the operator console renders in the exact
 * same visual system as the bank product. Extraction to a shared packages/ui
 * preset is planned; until then keep this file in sync with the dashboard's
 * by hand.
 *
 * Color names are re-pointed at the semantic CSS variables defined in
 * app/globals.css. Tokens are stored as RGB channel triplets, which keeps
 * Tailwind opacity modifiers working (e.g. `text-navy/85`).
 */
const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // `navy` is the SHARED heading alias with the bank dashboard: despite
        // the name it maps to --heading (near-white in this dark-only console),
        // used for headings and primary emphasis. DO NOT rename it — every page
        // (here and in the dashboard) reads `text-navy`, so a rename breaks
        // product parity and hundreds of call sites.
        navy: {
          DEFAULT: 'rgb(var(--heading) / <alpha-value>)',
          900: 'rgb(var(--nav-bg) / <alpha-value>)',
          800: 'rgb(var(--heading) / <alpha-value>)',
          700: 'rgb(var(--btn-primary-hover) / <alpha-value>)',
        },
        nav: {
          DEFAULT: 'rgb(var(--nav-bg) / <alpha-value>)',
        },
        teal: {
          DEFAULT: 'rgb(var(--btn-primary) / <alpha-value>)',
          dark: 'rgb(var(--nav-bg) / <alpha-value>)',
        },
        action: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          hover: 'rgb(var(--accent-hover) / <alpha-value>)',
          light: 'rgb(var(--accent-soft) / <alpha-value>)',
        },
        success: {
          DEFAULT: 'rgb(var(--ok) / <alpha-value>)',
          light: 'rgb(var(--ok-soft) / <alpha-value>)',
        },
        warning: {
          DEFAULT: 'rgb(var(--warn) / <alpha-value>)',
          light: 'rgb(var(--warn-soft) / <alpha-value>)',
        },
        critical: {
          DEFAULT: 'rgb(var(--crit) / <alpha-value>)',
          light: 'rgb(var(--crit-soft) / <alpha-value>)',
        },
        slate: {
          DEFAULT: 'rgb(var(--text-muted) / <alpha-value>)',
          light: 'rgb(var(--text-faint) / <alpha-value>)',
        },
        ink: {
          DEFAULT: 'rgb(var(--text) / <alpha-value>)',
        },
        surface: {
          DEFAULT: 'rgb(var(--surface-hover) / <alpha-value>)',
          alt: 'rgb(var(--bg) / <alpha-value>)',
          raised: 'rgb(var(--surface-raised) / <alpha-value>)',
          base: 'rgb(var(--surface) / <alpha-value>)',
        },
        border: {
          DEFAULT: 'rgb(var(--line-strong) / <alpha-value>)',
          light: 'rgb(var(--line) / <alpha-value>)',
        },
        focus: 'rgb(var(--focus) / <alpha-value>)',
      },
      fontFamily: {
        sans: [
          'var(--font-inter)',
          '-apple-system',
          'BlinkMacSystemFont',
          'sans-serif',
        ],
        mono: ['var(--font-plex-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // Dense enterprise typography scale
        'display': ['32px', { lineHeight: '40px', fontWeight: '600', letterSpacing: '-0.01em' }],
        'h1': ['24px', { lineHeight: '32px', fontWeight: '600' }],
        'h2': ['20px', { lineHeight: '28px', fontWeight: '600' }],
        'h3': ['16px', { lineHeight: '24px', fontWeight: '500' }],
        'body': ['14px', { lineHeight: '20px' }],
        'body-lg': ['16px', { lineHeight: '24px' }],
        'caption': ['12px', { lineHeight: '16px' }],
        'micro': ['11px', { lineHeight: '14px', letterSpacing: '0.04em' }],
        // KPI numerics (IBM Plex Mono accent sizes)
        'kpi': ['28px', { lineHeight: '34px', fontWeight: '600', letterSpacing: '-0.01em' }],
        'kpi-lg': ['36px', { lineHeight: '42px', fontWeight: '600', letterSpacing: '-0.01em' }],
      },
      boxShadow: {
        'subtle': 'var(--shadow-subtle)',
        'pop': 'var(--shadow-pop)',
      },
    },
  },
  plugins: [],
};

export default config;
