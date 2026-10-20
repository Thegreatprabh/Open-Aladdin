/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#06080D',
        panel: '#0B0F17',
        card: '#111827',
        edge: '#1E293B',
        amber: '#F59E0B',
        qcyan: '#06B6D4',
        bull: '#10B981',
        bear: '#F43F5E',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
};
