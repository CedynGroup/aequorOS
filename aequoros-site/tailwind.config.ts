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
        'text-primary': '#1A202C',
        'text-muted': '#64748B',
        'border-light': '#E2E8F0',
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
