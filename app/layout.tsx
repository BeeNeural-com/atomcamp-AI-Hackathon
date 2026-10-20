import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BeeProof AI — Every field, accounted for.",
  description: "An evidence-first workspace for extracting, validating, and reviewing financial rate sheets.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
