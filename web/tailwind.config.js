/** Brand tokens ported 1:1 from dashboard/views.py (the "Coverage Floor" look). */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        gold: "#D4AF37",
        "gold-l": "#F4D77A",
        ink: "#0D0D0D",
        dark: "#1F1F1F",
        bg: "#F5F5F5",
        surface: "#FFFFFF",
        muted: "#5a5a5a",
        border: "#e6e6e6",
        danger: "#b00020",
        good: "#1d7a3a",
      },
      fontFamily: {
        serif: ['"Playfair Display"', "Georgia", "serif"],
        sans: ['"Montserrat"', "-apple-system", "BlinkMacSystemFont", '"Segoe UI"', "Roboto", "sans-serif"],
      },
      borderRadius: { DEFAULT: "8px" },
    },
  },
  plugins: [],
};
