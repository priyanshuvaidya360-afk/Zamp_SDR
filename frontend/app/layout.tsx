import type { Metadata } from "next";
import { Geist_Mono, Plus_Jakarta_Sans, Source_Serif_4 } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/nav";

const jakarta = Plus_Jakarta_Sans({
  variable: "--font-sans", // the variable shadcn's theme reads (globals.css @theme)
  subsets: ["latin"],
});

// The email body reads like a letter: a serif, as in the design system.
const serif = Source_Serif_4({
  variable: "--font-serif",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Signal",
  description: "Prospect research and cited outreach drafts for SDRs",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${jakarta.variable} ${serif.variable} ${geistMono.variable} h-full font-sans antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background">
        <Nav />
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
