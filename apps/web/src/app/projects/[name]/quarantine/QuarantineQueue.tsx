"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { FlagPill } from "@/components/review/FlagPill";
import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { RowDetailDrawer } from "@/components/review/RowDetailDrawer";
import { StatusBadge } from "@/components/review/StatusBadge";
import { useToasts } from "@/components/review/ToastHost";
import type { QuarantinedItem } from "@/lib/api";
import {
  explainQueueError,
  queueClient,
  type EditDeliverableInput,
} from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "A", description: "Accept selected row" },
  { keys: "R", description: "Reject selected row" },
  { keys: "E", description: "Edit selected row" },
  { keys: "S", description: "Skip (next row)" },
  { keys: "← / →", description: "Previous / next row" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

interface QuarantineQueueProps {
  projectName: string;
  items: QuarantinedItem[];
}

type Mode = "view" | "edit" | "confirm-reject";

export function QuarantineQueue({ projectName, items }: QuarantineQueueProps) {
  const router = useRouter();
  const toasts = useToasts();

  const [selectedIdx, setSelectedIdx] = useState(0);
  const [mode, setMode] = useState<Mode>("view");
  const [busy, setBusy] = useState(false);
  const [editForm, setEditForm] = useState<EditDeliverableInput>({});

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

  const onAccept = useCallback(async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await queueClient.acceptDeliverable(projectName, selected.deliverable_id);
      toasts.success(`Accepted "${truncate(selected.summary)}".`);
      router.refresh();
    } catch (err) {
      toasts.error(explainQueueError(err), () => void onAccept());
    } finally {
      setBusy(false);
    }
  }, [selected, projectName, router, toasts]);

  const onRejectConfirmed = useCallback(async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await queueClient.rejectDeliverable(projectName, selected.deliverable_id);
      toasts.success(`Rejected "${truncate(selected.summary)}".`);
      setMode("view");
      router.refresh();
    } catch (err) {
      toasts.error(explainQueueError(err), () => void onRejectConfirmed());
    } finally {
      setBusy(false);
    }
  }, [selected, projectName, router, toasts]);

  const onEditSubmit = useCallback(async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await queueClient.editDeliverable(
        projectName,
        selected.deliverable_id,
        editForm,
      );
      toasts.success("Edit saved as a new deliverable revision.");
      setMode("view");
      router.refresh();
    } catch (err) {
      toasts.error(explainQueueError(err), () => void onEditSubmit());
    } finally {
      setBusy(false);
    }
  }, [selected, projectName, editForm, router, toasts]);

  const onShortcut = useCallback(
    (key: string) => {
      if (mode !== "view") return; // ignore while modal is up
      switch (key) {
        case "a":
          void onAccept();
          break;
        case "r":
          setMode("confirm-reject");
          break;
        case "e":
          if (selected) {
            setEditForm({
              summary: selected.summary,
              trade_value: selected.trade,
              service_value: selected.service,
              category_value: selected.category,
            });
            setMode("edit");
          }
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
    [mode, onAccept, selected, move],
  );

  const sourceFiles = useMemo(() => {
    const set = new Set<string>();
    items.forEach((i) => {
      if (i.source_filename) set.add(i.source_filename);
    });
    return Array.from(set).sort();
  }, [items]);

  return (
    <div className="space-y-4">
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} onAction={onShortcut} />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-elevated px-4 py-2 text-xs text-text-muted">
        <span>
          {items.length} quarantined • {sourceFiles.length} source
          {sourceFiles.length === 1 ? "" : "s"} • Showing row{" "}
          {Math.min(selectedIdx + 1, items.length)} of {items.length}
        </span>
        <span>
          Tip: press <kbd className="rounded border border-border px-1">?</kbd>{" "}
          for shortcuts.
        </span>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_2fr]">
        <ul className="max-h-[70vh] space-y-1 overflow-y-auto rounded-md border border-border bg-surface-elevated p-2">
          {items.map((it, i) => (
            <li key={it.deliverable_id}>
              <button
                type="button"
                onClick={() => setSelectedIdx(i)}
                className={`flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left text-sm transition ${
                  i === selectedIdx
                    ? "border-accent/60 bg-accent/10 text-text-primary"
                    : "border-transparent hover:border-border hover:bg-surface text-text-muted"
                }`}
              >
                <span className="line-clamp-2 leading-snug">{it.summary}</span>
                <span className="flex flex-wrap items-center gap-1 text-[10px]">
                  <StatusBadge label={it.confidence} variant="confidence" />
                  <StatusBadge label={it.gate_outcome} variant="gate" />
                  {it.flags.slice(0, 2).map((f) => (
                    <FlagPill key={f} flag={f} />
                  ))}
                  {it.flags.length > 2 ? (
                    <span className="text-text-muted">
                      +{it.flags.length - 2} more
                    </span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="rounded-md border border-border bg-surface-elevated p-5">
          {selected ? (
            <DetailPanel
              item={selected}
              busy={busy}
              onAccept={onAccept}
              onReject={() => setMode("confirm-reject")}
              onEdit={() => {
                setEditForm({
                  summary: selected.summary,
                  trade_value: selected.trade,
                  service_value: selected.service,
                  category_value: selected.category,
                });
                setMode("edit");
              }}
              onSkip={() => move(1)}
            />
          ) : null}
        </div>
      </div>

      <RowDetailDrawer
        open={mode === "edit"}
        title="Edit deliverable"
        subtitle={selected?.source_filename ?? undefined}
        onClose={() => (busy ? undefined : setMode("view"))}
        footer={
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setMode("view")}
              disabled={busy}
              className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onEditSubmit}
              disabled={busy}
              className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save edit"}
            </button>
          </div>
        }
      >
        <EditForm form={editForm} setForm={setEditForm} />
      </RowDetailDrawer>

      <ConfirmDialog
        open={mode === "confirm-reject"}
        title="Reject this deliverable?"
        body={
          <span>
            <strong>“{truncate(selected?.summary ?? "")}”</strong> will be
            removed from the master register and marked{" "}
            <code>user_rejected</code>. The audit trail keeps the row, so you
            can promote it again from the Audit queue if you change your mind.
          </span>
        }
        confirmLabel="Reject deliverable"
        busy={busy}
        onCancel={() => (busy ? undefined : setMode("view"))}
        onConfirm={onRejectConfirmed}
      />
    </div>
  );
}

function DetailPanel({
  item,
  busy,
  onAccept,
  onReject,
  onEdit,
  onSkip,
}: {
  item: QuarantinedItem;
  busy: boolean;
  onAccept: () => void;
  onReject: () => void;
  onEdit: () => void;
  onSkip: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-base font-semibold leading-snug text-text-primary">
          {item.summary}
        </h2>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <StatusBadge label={item.status} variant="status" />
          <StatusBadge label={item.gate_outcome} variant="gate" />
          <StatusBadge label={item.confidence} variant="confidence" />
          {item.flags.map((f) => (
            <FlagPill key={f} flag={f} />
          ))}
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <Field label="Source">{item.source_filename ?? item.source_id}</Field>
        <Field label="Created">{new Date(item.created_at).toLocaleString()}</Field>
        <Field label="Trade">{item.trade ?? "—"}</Field>
        <Field label="Service">{item.service ?? "—"}</Field>
        <Field label="Category">{item.category ?? "—"}</Field>
        <Field label="Extraction group">
          <span className="font-mono">{item.extraction_group_id}</span>
        </Field>
      </dl>

      {Object.keys(item.flag_context).length > 0 ? (
        <details className="rounded border border-border bg-surface p-3 text-xs">
          <summary className="cursor-pointer text-text-muted">
            Flag context ({Object.keys(item.flag_context).length})
          </summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-[11px] text-text-muted">
            {JSON.stringify(item.flag_context, null, 2)}
          </pre>
        </details>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <button
          type="button"
          onClick={onAccept}
          disabled={busy}
          className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          Accept (A)
        </button>
        <button
          type="button"
          onClick={onEdit}
          disabled={busy}
          className="rounded-full border border-border px-4 py-2 text-sm text-text-primary hover:border-text-muted disabled:opacity-50"
        >
          Edit (E)
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          className="rounded-full border border-red-500/50 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 disabled:opacity-50"
        >
          Reject (R)
        </button>
        <button
          type="button"
          onClick={onSkip}
          disabled={busy}
          className="ml-auto rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary disabled:opacity-50"
        >
          Skip (S) →
        </button>
      </div>
    </div>
  );
}

function EditForm({
  form,
  setForm,
}: {
  form: EditDeliverableInput;
  setForm: (f: EditDeliverableInput) => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-text-muted">
        Edits are non-destructive — saving creates a new revision linked to the
        original. The master register only shows the latest revision.
      </p>
      <Input
        label="Summary"
        value={form.summary ?? ""}
        onChange={(v) => setForm({ ...form, summary: v })}
        textarea
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Input
          label="Trade"
          value={form.trade_value ?? ""}
          onChange={(v) => setForm({ ...form, trade_value: v })}
        />
        <Input
          label="Service"
          value={form.service_value ?? ""}
          onChange={(v) => setForm({ ...form, service_value: v })}
        />
        <Input
          label="Category"
          value={form.category_value ?? ""}
          onChange={(v) => setForm({ ...form, category_value: v })}
        />
      </div>
      <p className="text-[11px] text-text-muted">
        Typing a brand-new trade / service / category counts as confirming that
        value — it&apos;ll be added to the project taxonomy.
      </p>
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

function truncate(s: string, n = 80): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
