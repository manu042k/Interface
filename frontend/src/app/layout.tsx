import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/lib/providers";
import { AppShell } from "@/components/app-shell";

const inter = Inter({ variable: "--font-sans", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Trace",
  description:
    "LLM-driven discovery, deterministic replay, live sandbox, human handoff.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="bg-background text-foreground min-h-full">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
