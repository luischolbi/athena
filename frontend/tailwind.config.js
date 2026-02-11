/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        athena: {
          bg: '#0a0a0f',
          card: '#161b22',
          'card-hover': '#1c2333',
          border: 'rgba(255,255,255,0.06)',
          'border-hover': 'rgba(59,130,246,0.3)',
          text: '#e6e6e6',
          muted: '#8b8b9e',
          accent: '#3b82f6',
          'accent-hover': '#60a5fa',
          green: '#4ade80',
          amber: '#f59e0b',
        },
      },
      fontFamily: {
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 0.4s ease-out forwards',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}
