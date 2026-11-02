import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "Agent HRM", description: "Time off, payslips and HR requests" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
