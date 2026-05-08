import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // From AequorOS Figma Design Brief — banking palette
        navy: {
          DEFAULT: '#0A2540',
          900: '#061629',
          800: '#0A2540',
          700: '#143055',
        },
        teal: {
          DEFAULT: '#1A4D5C',
          dark: '#0F3340',
        },
        action: {
          DEFAULT: '#2D7FF9',
          hover: '#1F6CE0',
          light: '#E8F0FE',
        },
        success: {
          DEFAULT: '#0E8A4F',
          light: '#E5F4EC',
        },
        warning: {
          DEFAULT: '#C97C00',
          light: '#FBF1DF',
        },
        critical: {
          DEFAULT: '#B3261E',
          light: '#FBE9E7',
        },
        slate: {
          DEFAULT: '#5A6776',
          light: '#7A8693',
        },
        surface: {
          DEFAULT: '#F5F7FA',
          alt: '#FAFBFC',
        },
        border: {
          DEFAULT: '#D0D7DE',
          light: '#E4E8EC',
        },
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
        // Banking UI typography scale
        'display': ['32px', { lineHeight: '40px', fontWeight: '600', letterSpacing: '-0.01em' }],
        'h1': ['24px', { lineHeight: '32px', fontWeight: '600' }],
        'h2': ['20px', { lineHeight: '28px', fontWeight: '600' }],
        'h3': ['16px', { lineHeight: '24px', fontWeight: '500' }],
        'body': ['14px', { lineHeight: '20px' }],
        'body-lg': ['16px', { lineHeight: '24px' }],
        'caption': ['12px', { lineHeight: '16px' }],
        'micro': ['11px', { lineHeight: '14px', letterSpacing: '0.04em' }],
      },
      boxShadow: {
        'subtle': '0 1px 2px rgba(10, 37, 64, 0.04), 0 1px 3px rgba(10, 37, 64, 0.06)',
        'pop': '0 4px 12px rgba(10, 37, 64, 0.08)',
      },
    },
  },
  plugins: [],
};

export default config;
