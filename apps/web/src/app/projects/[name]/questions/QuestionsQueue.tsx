"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { useToasts } from "@/components/review/ToastHost";
import type { QuestionItem } from "@/lib/api";
import { explainQueueError, queueClient } from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "A", description: "Focus the resolution field" },
  { keys: "R", description: "Submit resolution for selected question" },
  { keys: "S", description: "Skip (next question)" },
  { keys: "← / →", description: "Previous / next question" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

export function QuestionsQueue({
  projectName,
  items,
}: {
  projectName: string;
  items: QuestionItem[];
}) {
  const router = useRouter();
  const toasts = useToasts();

  const [selectedIdx, setSelectedIdx] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

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

  const submit = useCallback(
    async (q: QuestionItem) => {
      const answer = (answers[q.id] ?? "").trim();
      if (!answer) {
        toasts.error("Please type a resolution before submitting.");
        return;
      }
      setBusyId(q.id);
      try {
        await queueClient.resolveQuestion(projectName, q.id, {
          resolution_payload: { answer, resolved_at_ui: true },
          mark_status: "resolved",
        });
        toasts.success("Question resolved.");
        router.refresh();
      } catch (err) {
        toasts.error(explainQueueError(err), () => void submit(q));
      } finally {
        setBusyId(null);
      }
    },
    [answers, projectName, router, toasts],
  );

  const dismiss = useCallback(
    async (q: QuestionItem) => {
      setBusyId(q.id);
      try {
        await queueClient.dismissQuestion(projectName, q.id, {
          reason: "Dismissed from review UI.",
        });
        toasts.success("Question dismissed.");
        router.refresh();
      } catch (err) {
        toasts.error(explainQueueError(err), () => void dismiss(q));
      } finally {
        setBusyId(null);
      }
    },
    [projectName, router, toasts],
  );

  const onShortcut = useCallback(
    (key: string) => {
      if (!selected) return;
      switch (key) {
        case "a": {
          const el = document.getElementById(`q-${selected.id}-answer`);
          if (el instanceof HTMLTextAreaElement) el.focus();
          break;
        }
        case "r":
          void submit(selected);
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
    [selected, submit, move],
  );

  return (
    <div className="space-y-4">
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} onAction={onShortcut} />

      <div className="rounded-md border border-border bg-surface-elevated px-4 py-2 text-xs text-text-muted">
        {items.length} pending question{items.length === 1 ? "" : "s"}
      </div>

      <ul className="space-y-3">
        {items.map((q, i) => {
          const isSelected = i === selectedIdx;
          const isBusy = busyId === q.id;
          return (
            <li
              key={q.id}
              onClick={() => setSelectedIdx(i)}
              className={`cursor-pointer rounded-md border p-4 transition ${
                isSelected
                  ? "border-accent/60 bg-accent/5"
                  : "border-border bg-surface-elevated"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <span className="inline-block rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-text-muted">
                    {q.kind}
                  </span>
                  <h3 className="text-sm font-semibold text-text-primary">
                    {q.question_text}
                  </h3>
                  <p className="text-xs text-text-muted">{q.context}</p>
                </div>
                <span className="shrink-0 text-[11px] text-text-muted">
                  {new Date(q.created_at).toLocaleDateString()}
                </span>
              </div>

              {q.proposed_resolution ? (
                <p className="mt-3 rounded border border-border bg-background p-2 text-xs text-text-muted">
                  <span className="font-semibold text-text-primary">
                    AI proposal:{" "}
                  </span>
                  {q.proposed_resolution}
                </p>
              ) : null}

              <div className="mt-3 space-y-2">
                <label
                  htmlFor={`q-${q.id}-answer`}
                  className="block text-xs uppercase tracking-wide text-text-muted"
                >
                  Your resolution
                </label>
                <textarea
                  id={`q-${q.id}-answer`}
                  value={answers[q.id] ?? q.proposed_resolution ?? ""}
                  onChange={(e) =>
                    setAnswers({ ...answers, [q.id]: e.target.value })
                  }
                  rows={3}
                  className="w-full rounded border border-border bg-background px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none"
                  placeholder="e.g. 'Use revision C, the later one supersedes the earlier draft.'"
                />
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void submit(q);
                  }}
                  disabled={isBusy}
                  className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                >
                  {isBusy ? "Submitting…" : "Resolve"}
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void dismiss(q);
                  }}
                  disabled={isBusy}
                  className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary disabled:opacity-50"
                >
                  Dismiss
                </button>
                {q.candidate_source_refs.length > 0 ? (
                  <span className="ml-auto text-[11px] text-text-muted">
                    {q.candidate_source_refs.length} source ref(s)
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
