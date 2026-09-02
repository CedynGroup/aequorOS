import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'navy-deep': '#0F1845',
        navy: '#1E2761',
        accent: '#4FC3F7',
        'ice-blue': '#CADCFC',
        'soft-bg': '#F8FAFC',
        'text-primary': '#16203A',
        'text-muted': '#5B6478',
        'border-light': '#E2E8F0',
        // Redesign system (2026-09): warmed neutrals + product-derived action.
        paper: '#FBFAF7',
        stone: '#F2F0E9',
        ink: '#16203A',
        'ink-soft': '#3D4560',
        hairline: '#E4E2DB',
        kicker: '#B45309',
        action: '#2D7FF9',
        'action-dark': '#1F6CE0',
        live: '#10B981',
        watch: '#FBBF24',
      },
      fontFamily: {
        serif: ['var(--font-fraunces)', 'Georgia', 'serif'],
        sans: [
          'var(--font-inter)',
          '-apple-system',
          'BlinkMacSystemFont',
          'sans-serif',
        ],
      },
      maxWidth: {
        '8xl': '1200px',
      },
    },
  },
  plugins: [],
};

export default config;
