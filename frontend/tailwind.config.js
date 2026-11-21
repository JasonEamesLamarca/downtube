/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'media',
  theme: {
    extend: {
      colors: { brand: { 50: '#fef2f2', 500: '#e11d48', 600: '#be123c', 700: '#9f1239' } },
    },
  },
  plugins: [],
};
