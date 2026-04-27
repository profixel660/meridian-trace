"use client";

import { useMemo, useState } from "react";

import { FlagPill } from "@/components/review/FlagPill";
import { StatusBadge } from "@/components/review/StatusBadge";
import type { MasterRow } from "@/lib/api";

function uniqueSorted(values: Array<string | null>): string[] {
  const set = new Set<string>();
  values.forEach((v) => {
    if (v) set.add(v);
  });
  return Array.from(set).sort();
}

export function MasterTable({ rows }: { rows: MasterRow[] }) {
  const [trade, setTrade] = useState<string>("");
  const [service, setService] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [q, setQ] = useState("");

  const trades = useMemo(() => uniqueSorted(rows.map((r) => r.trade)), [rows]);
  const services = useMemo(
    () => uniqueSorted(rows.map((r) => r.service)),
    [rows],
  );
  const categories = useMemo(
    () => uniqueSorted(rows.map((r) => r.category)),
    [rows],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (trade && r.trade !== trade) return false;
      if (service && r.service !== service) return false;
      if (category && r.category !== category) return false;
      if (needle && !(r.summary ?? "").toLowerCase().includes(needle)) {
        return false;
      }
      return true;
    });
  }, [rows, trade, service, category, q]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface-elevated p-4">
        <Filter
          label="Trade"
          value={trade}
          onChange={setTrade}
          options={trades}
        />
        <Filter
          label="Service"
          value={service}
          onChange={setService}
          options={services}
        />
        <Filter
          label="Category"
          value={category}
          onChange={setCategory}
          options={categories}
        />
        <label className="flex flex-col text-xs">
          <span className="uppercase tracking-wide text-text-muted">
            Search summary
          </span>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. ductwork"
            className="mt-1 rounded border border-border bg-background px-3 py-1.5 text-sm text-text-primary focus:border-accent focus:outline-none"
          />
        </label>
        <span className="ml-auto text-xs text-text-muted">
          Showing {filtered.length} of {rows.length}
        </span>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="min-w-full divide-y divide-border text-sm">
          <thead className="bg-surface-elevated text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <Th>Summary</Th>
              <Th>Trade</Th>
              <Th>Service</Th>
              <Th>Category</Th>
              <Th>Confidence</Th>
              <Th>Source</Th>
              <Th>Flags</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border bg-surface">
            {filtered.map((r) => (
              <tr key={r.id} className="align-top">
                <Td>
                  <div className="max-w-xl whitespace-pre-wrap leading-snug text-text-primary">
                    {r.summary ?? "—"}
                  </div>
                  {r.applicable_standards.length > 0 ? (
                    <div className="mt-1 text-[11px] text-text-muted">
                      Standards: {r.applicable_standards.join(", ")}
                    </div>
                  ) : null}
                </Td>
                <Td>{r.trade ?? "—"}</Td>
                <Td>{r.service ?? "—"}</Td>
                <Td>{r.category ?? "—"}</Td>
                <Td>
                  {typeof r.confidence === "string" ? (
                    <StatusBadge label={r.confidence} variant="confidence" />
                  ) : r.confidence !== null ? (
                    <span className="font-mono text-xs text-text-primary">
                      {r.confidence}
                    </span>
                  ) : (
                    "—"
                  )}
                </Td>
                <Td>
                  <span className="text-xs text-text-muted">
                    {r.source_document ?? "—"}
                  </span>
                  {r.document_state ? (
                    <div className="text-[10px] text-text-muted">
                      {r.document_state}
                    </div>
                  ) : null}
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {r.flags.map((f) => (
                      <FlagPill key={f} flag={f} />
                    ))}
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Filter({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <label className="flex flex-col text-xs">
      <span className="uppercase tracking-wide text-text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 rounded border border-border bg-background px-2 py-1.5 text-sm text-text-primary focus:border-accent focus:outline-none"
      >
        <option value="">All</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 text-left font-medium">{children}</th>;
}

function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-3 py-2 text-text-primary">{children}</td>;
}
