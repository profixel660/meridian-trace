"use client";

import Link from "next/link";

import type { ConflictItem } from "@/lib/api";

const RESOLUTION_LABEL: Record<string, string> = {
  pending: "—",
  resolved_accept_a: "Accept A",
  resolved_accept_b: "Accept B",
  resolved_reject_both: "Reject both",
  resolved_hybrid: "Hybrid",
  superseded: "—",
};

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  pending:              { label: "⚠ Pending",   cls: "bg-amber-500/15 text-amber-300" },
  resolved_accept_a:    { label: "✓ Resolved",  cls: "bg-emerald-500/15 text-emerald-300" },
  resolved_accept_b:    { label: "✓ Resolved",  cls: "bg-emerald-500/15 text-emerald-300" },
  resolved_reject_both: { label: "✓ Resolved",  cls: "bg-emerald-500/15 text-emerald-300" },
  resolved_hybrid:      { label: "✓ Resolved",  cls: "bg-emerald-500/15 text-emerald-300" },
  superseded:           { label: "Superseded", cls: "bg-gray-500/15 text-gray-300" },
};

interface Props {
  projectSlug: string;
  items: ConflictItem[];
}

export function ConflictRegisterTable({ projectSlug, items }: Props) {
  const base = `/projects/${encodeURIComponent(projectSlug)}`;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-xs">
        <thead className="bg-surface-elevated text-left text-text-muted">
          <tr>
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">Source A</th>
            <th className="px-3 py-2">Value A</th>
            <th className="px-3 py-2">Source B</th>
            <th className="px-3 py-2">Value B</th>
            <th className="px-3 py-2">Kind</th>
            <th className="px-3 py-2">Most-onerous reasoning</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Resolution</th>
            <th className="px-3 py-2">Resolved at</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c, idx) => {
            const partyA = c.parties[0];
            const partyB = c.parties[1];
            const badge = STATUS_BADGE[c.status] ?? { label: c.status, cls: "" };
            const resolution = RESOLUTION_LABEL[c.status] ?? "—";
            return (
              <tr key={c.id} className="border-t border-border">
                <td className="px-3 py-2 text-text-muted">{idx + 1}</td>
                <td className="px-3 py-2 text-text-muted font-mono text-[10px]">
                  {partyA?.source_filename ?? "—"}
                </td>
                <td className="px-3 py-2 text-text-primary whitespace-pre-wrap">
                  {partyA?.party_position ?? ""}
                </td>
                <td className="px-3 py-2 text-text-muted font-mono text-[10px]">
                  {partyB?.source_filename ?? "—"}
                </td>
                <td className="px-3 py-2 text-text-primary whitespace-pre-wrap">
                  {partyB?.party_position ?? ""}
                </td>
                <td className="px-3 py-2 text-text-muted">{c.kind}</td>
                <td className="px-3 py-2 text-text-primary whitespace-pre-wrap">
                  {c.most_onerous_reasoning}
                </td>
                <td className="px-3 py-2">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${badge.cls}`}>
                    {badge.label}
                  </span>
                </td>
                <td className="px-3 py-2 text-text-muted">
                  {c.status === "pending" ? (
                    <Link
                      href={`${base}/conflicts?focus=${c.id}`}
                      className="text-accent hover:underline"
                    >
                      Resolve →
                    </Link>
                  ) : (
                    resolution
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-[10px] text-text-muted">
                  {c.resolved_at ?? c.created_at}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {items.length === 0 && (
        <div className="px-3 py-4 text-center text-text-muted">No conflicts in this filter.</div>
      )}
    </div>
  );
}
