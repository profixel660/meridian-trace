"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { RowDetailDrawer } from "@/components/review/RowDetailDrawer";
import { useToasts } from "@/components/review/ToastHost";
import type { AuditItem } from "@/lib/api";
import { explainQueueError, queueClient } from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "A", description: "Open promote form for selected row" },
  { keys: "S", description: "Skip (next row)" },
  { keys: "← / →", description: "Previous / next row" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

interface PromoteForm {
  trade_value: string;
  service_value: string;
  category_value: string;
  summary: string;
}

const EMPTY_FORM: PromoteForm = {
  trade_value: "",
  service_value: "",
  category_value: "",
  summary: "",
};

export function AuditQueue({
  projectName,
  items,
}: {
  projectName: string;
  items: AuditItem[];
}) {
  const router = useRouter();
  const toasts = useToasts();

  const [selectedIdx, setSelectedIdx] = useState(0);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [form, setForm] = useState<PromoteForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);

  const selected = items[selectedIdx] ?? null;

  const move = useCallback(
    (delta: number) => {
      setSelectedIdx((idx) => {
        const n = items.length;
        if (n === 0) return 0;
        return (idx + delta + n) % n;
      });
    },
    [items.length],
  );

  const openPromote = useCallback(() => {
    if (!selected) return;
    setForm({
      ...EMPTY_FORM,
      summary: selected.candidate_text.slice(0, 200),
    });
    setPromoteOpen(true);
  }, [selected]);

  const submitPromote = useCallback(async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await queueClient.promoteAudit(projectName, selected.id, {
        trade_value: form.trade_value || null,
        service_value: form.service_value || null,
        category_value: form.category_value || null,
        summary: form.summary || null,
      });
      toasts.success("Promoted to a new deliverable on the master register.");
      setPromoteOpen(false);
      router.refresh();
    } catch (err) {
      toasts.error(explainQueueError(err), () => void submitPromote());
    } finally {
      setBusy(false);
    }
  }, [selected, projectName, form, router, toasts]);

  const onShortcut = useCallback(
    (key: string) => {
      if (promoteOpen) return;
      switch (key) {
        case "a":
          openPromote();
          break;
        case "s":
        case "ArrowRight":
          move(1);
          break;
        case "ArrowLeft":
          move(-1);
          break;
      }
    },
    [promoteOpen, openPromote, move],
  );

  return (
    <div className="space-y-4">
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} onAction={onShortcut} />

      <div className="rounded-md border border-border bg-surface-elevated px-4 py-2 text-xs text-text-muted">
        {items.length} OUTSIDE rows — newest first.
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_2fr]">
        <ul className="max-h-[70vh] space-y-1 overflow-y-auto rounded-md border border-border bg-surface-elevated p-2">
          {items.map((it, i) => (
            <li key={it.id}>
              <button
                type="button"
                onClick={() => setSelectedIdx(i)}
                className={`flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left text-sm transition ${
                  i === selectedIdx
                    ? "border-accent/60 bg-accent/10 text-text-primary"
                    : "border-transparent hover:border-border hover:bg-surface text-text-muted"
                }`}
              >
                <span className="line-clamp-2 leading-snug">
                  {it.candidate_text || "(no text extracted)"}
                </span>
                <span className="text-[10px] text-text-muted">
                  {it.source_document || it.source_id} •{" "}
                  {new Date(it.created_at).toLocaleDateString()}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="rounded-md border border-border bg-surface-elevated p-5">
          {selected ? (
            <div className="space-y-4">
              <h2 className="text-base font-semibold text-text-primary">
                Candidate text
              </h2>
              <p className="whitespace-pre-wrap rounded border border-border bg-background p-3 text-sm leading-relaxed text-text-primary">
                {selected.candidate_text}
              </p>

              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                <Field label="Source">
                  {selected.source_document || selected.source_id}
                </Field>
                <Field label="Created">
                  {new Date(selected.created_at).toLocaleString()}
                </Field>
                <Field label="Reason">{selected.rejection_reason}</Field>
                <Field label="Landlord response">
                  {selected.landlord_response ?? "—"}
                </Field>
              </dl>

              {selected.user_promoted_to_deliverable_id ? (
                <p className="rounded border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-200">
                  Already promoted to deliverable{" "}
                  <code>{selected.user_promoted_to_deliverable_id}</code>.
                </p>
              ) : null}

              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <button
                  type="button"
                  onClick={openPromote}
                  disabled={busy || !!selected.user_promoted_to_deliverable_id}
                  className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                >
                  Promote (A)
                </button>
                <button
                  type="button"
                  onClick={() => move(1)}
                  className="ml-auto rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary"
                >
                  Skip (S) →
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <RowDetailDrawer
        open={promoteOpen}
        title="Promote audit row to deliverable"
        subtitle={selected?.source_document}
        onClose={() => (busy ? undefined : setPromoteOpen(false))}
        footer={
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setPromoteOpen(false)}
              disabled={busy}
              className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submitPromote}
              disabled={busy}
              className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Promoting…" : "Promote to master"}
            </button>
          </div>
        }
      >
        <PromoteForm form={form} setForm={setForm} />
      </RowDetailDrawer>
    </div>
  );
}

function PromoteForm({
  form,
  setForm,
}: {
  form: PromoteForm;
  setForm: (f: PromoteForm) => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-text-muted">
        Promotion creates a brand-new deliverable in <code>user_promoted</code>{" "}
        status. Tag the trade / service / category so the master register
        groups it correctly.
      </p>
      <Input
        label="Summary (override candidate text)"
        value={form.summary}
        onChange={(v) => setForm({ ...form, summary: v })}
        textarea
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Input
          label="Trade"
          value={form.trade_value}
          onChange={(v) => setForm({ ...form, trade_value: v })}
        />
        <Input
          label="Service"
          value={form.service_value}
          onChange={(v) => setForm({ ...form, service_value: v })}
        />
        <Input
          label="Category"
          value={form.category_value}
          onChange={(v) => setForm({ ...form, category_value: v })}
        />
      </div>
    </div>
  );
}

function Input({
  label,
  value,
  onChange,
  textarea = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  textarea?: boolean;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs uppercase tracking-wide text-text-muted">
        {label}
      </span>
      {textarea ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          className="w-full rounded border border-border bg-background px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none"
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded border border-border bg-background px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none"
        />
      )}
    </label>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-border/60 pb-1">
      <dt className="text-[10px] uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="text-sm text-text-primary">{children}</dd>
    </div>
  );
}
