"use client";

import {
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import Link from "next/link";

interface TooltipProps {
  /** What goes inside the tooltip bubble. */
  content: ReactNode;
  /** Trigger element. Wrapped element gets aria-describedby + handlers. */
  children: ReactElement;
  /** Optional fixed width override (Tailwind class). Default `w-64`. */
  widthClass?: string;
}

/**
 * Accessible hover/focus tooltip.
 *
 * - Opens on hover (mouseenter), focus (keyboard tab), or click.
 * - Closes on mouseleave + blur, click outside, or Escape.
 * - Announces content via `aria-describedby` so screen readers hear it.
 *
 * Discoverability rule #1: every non-self-explanatory chip in the review
 * UI uses this; FlagPill / StatusBadge wrap it.
 */
export function Tooltip({ content, children, widthClass = "w-64" }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (
        wrapRef.current &&
        e.target instanceof Node &&
        !wrapRef.current.contains(e.target)
      ) {
        close();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  const trigger = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        "aria-describedby": open ? id : undefined,
        onMouseEnter: () => setOpen(true),
        onMouseLeave: () => setOpen(false),
        onFocus: () => setOpen(true),
        onBlur: () => setOpen(false),
        onClick: (e: React.MouseEvent) => {
          e.stopPropagation();
          setOpen((v) => !v);
        },
      } as Record<string, unknown>)
    : children;

  return (
    <span ref={wrapRef} className="relative inline-block">
      {trigger}
      {open ? (
        <span
          role="tooltip"
          id={id}
          className={`pointer-events-auto absolute left-1/2 z-30 mt-2 ${widthClass} -translate-x-1/2 rounded-md border border-border bg-surface-elevated p-3 text-xs leading-relaxed text-text-primary shadow-lg`}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Optional "More →" link rendered inside a Tooltip's content area.
 * Anchors to the glossary page; supports `#term` deep links.
 */
export function TooltipMore({
  href,
  children = "More →",
}: {
  href: string;
  children?: ReactNode;
}) {
  return (
    <Link
      href={href}
      className="mt-2 block text-[11px] font-medium text-accent hover:underline"
    >
      {children}
    </Link>
  );
}
