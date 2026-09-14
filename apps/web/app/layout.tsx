import type { Metadata } from "next";
import "./style.css";
export const metadata: Metadata = {
  title: "Pixel Shorts Factory — Your tiny team. Big ideas.",
  description:
    "A living pixel office connected to your real Shorts production server.",
  manifest: `${process.env.NEXT_PUBLIC_BASE_PATH || ""}/manifest.webmanifest`,
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
