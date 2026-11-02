import type { Config } from "tailwindcss";

// tokens from DESIGN.md
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F4F5F7",
        surface: "#FFFFFF",
        ink: { DEFAULT: "#1A1E29", soft: "#5A6172", faint: "#8A90A0" },
        rule: { DEFAULT: "#E2E5EB", strong: "#CDD2DB" },
        dossier: { DEFAULT: "#2B428F", hover: "#22367A", tint: "#ECEFF8" },
        ok: "#2E7A4E",
        wait: "#A86C14",
        no: "#B03A2E",
        off: "#8A90A0",
      },
      fontFamily: { sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"] },
      fontSize: {
        xs: ["12px", "16px"],
        sm: ["13px", "18px"],
        base: ["14px", "21px"],
        md: ["16px", "24px"],
        lg: ["20px", "28px"],
        xl: ["28px", "34px"],
      },
      borderRadius: { sm: "4px", DEFAULT: "6px", lg: "10px" },
    },
  },
  plugins: [],
};
export default config;
