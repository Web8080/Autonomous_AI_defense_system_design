import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Defense System Dashboard",
  description: "Autonomous AI Defense for Critical Infrastructure",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
