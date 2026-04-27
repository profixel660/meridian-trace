// Server-only segment layout for the `/projects/[name]/...` subtree.
//
// Round-16 Path A (docs/DECISIONS.md §3.7): the Next.js shell is built with
// `output: "export"` so it can be bundled into a Tauri MSI as a pure static
// site. Static export requires `generateStaticParams` on every dynamic
// segment. Project names are runtime-discovered (the SME creates them inside
// the desktop app), so we seed a single placeholder param at build time —
// the Tauri webview opens `index.html` at the SPA root and all real
// project navigation happens client-side via Next.js's router. Direct URL
// loads outside the app aren't a supported entry path.
export async function generateStaticParams(): Promise<{ name: string }[]> {
  return [{ name: "_" }];
}

export default function ProjectNameLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
