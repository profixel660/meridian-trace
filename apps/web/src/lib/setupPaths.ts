/**
 * Pure helpers for the setup wizard's typed-path prompt.
 *
 * Extracted from `first-documents/page.tsx` (alpha-10 grid audit
 * finding: inline path construction couldn't be unit-tested; pure
 * functions in their own module can).
 *
 * No DOM access, no React, no IO — every function in this file must be
 * deterministic given its inputs. That's the contract that makes them
 * worth extracting.
 */

/**
 * True iff ``p`` is shaped like an absolute filesystem path the
 * Meridian backend can resolve via Python's ``pathlib.Path``.
 *
 * Accepted shapes:
 *   - Windows drive letter:  ``C:\…``  or  ``C:/…``  (case-insensitive)
 *   - Windows UNC:           ``\\server\share\…``
 *   - POSIX absolute:        ``/foo/bar``
 *
 * Refused (intentionally — backend would 400 anyway):
 *   - Bare folder name:      ``MyProject``
 *   - Relative path:         ``./foo`` or ``foo/bar``
 *   - Env-var-prefixed:      ``%USERPROFILE%\Foo`` or ``$HOME/foo``
 *     (we don't expand these client-side; the backend's
 *      ``Path.expanduser()`` only handles ``~`` not ``%VAR%``)
 *
 * Pure function — same input, same output, no side effects.
 */
export function looksAbsolute(p: string): boolean {
  // Windows drive-letter path: "C:\..." or "C:/..."
  if (/^[A-Za-z]:[\\/]/.test(p)) return true;
  // Windows UNC: "\\server\share\..."
  if (/^\\\\/.test(p)) return true;
  // POSIX absolute: "/foo/bar"
  if (p.startsWith("/")) return true;
  return false;
}

/**
 * Strip surrounding ASCII double-quotes from ``p``, if any.
 *
 * Windows 11's "Copy as path" (Win+Shift+C in Explorer) wraps the path
 * in double-quotes — useful when pasting into a shell, problematic when
 * pasting into a text input that expects a bare path string. Alpha-9
 * users who pasted a copied-from-Explorer path saw a confusing
 * "doesn't look like a full path" error because position 0 was ``"``
 * not a letter. Alpha-10 strips the quotes BEFORE the absolute-path
 * check so the user-friendly path comes through unchanged.
 *
 * Only strips OUTER quotes; quoted segments mid-path (rare but valid
 * in NTFS) survive.
 *
 * Pure function.
 */
export function stripSurroundingQuotes(p: string): string {
  return p.replace(/^"+|"+$/g, "");
}

/**
 * Build a smart pre-fill for the browser-fallback typed-path input.
 *
 * Browser ``webkitdirectory`` only exposes the picked folder's NAME (no
 * absolute path) for security. Without help, the user sees just
 * ``Syd02 document repository`` in the input and is left to guess the
 * full Windows path. With this helper, we use the OS user's home dir
 * (returned by ``GET /setup/defaults``) to construct
 * ``<home>\Documents\<folderName>`` — correct for the ~90% case
 * (project folders kept under Documents), one edit if the project
 * lives elsewhere.
 *
 * Falls back to just ``folderName`` when ``homeDir`` is null/empty
 * (alpha-8-or-earlier backend that doesn't return ``home_dir``).
 *
 * Separator detection: prefers ``\`` if homeDir contains it, else
 * ``/``. Mixed-separator homeDirs (rare on Windows but possible if
 * Path.home() returns ``C:\Users/Foo``) get ``\`` consistently —
 * Python's Path normalises either way on the backend.
 *
 * Pure function.
 */
export function buildPrefilledPath(
  homeDir: string | null | undefined,
  folderName: string,
): string {
  if (!folderName) return "";
  if (!homeDir) return folderName;
  const sep = homeDir.includes("\\") ? "\\" : "/";
  // Trim trailing separator from homeDir to avoid `C:\\Users\\Foo\\\\Documents`.
  const trimmedHome = homeDir.replace(/[\\/]+$/, "");
  return `${trimmedHome}${sep}Documents${sep}${folderName}`;
}

/**
 * Alpha-13 — broader paste normalisation.
 *
 * Real-world copy-from-Explorer patterns the user pastes into the
 * typed-path input that the backend cannot resolve as-is:
 *
 *   * ``"C:\\Foo\\Bar"``        — Win+Shift+C wraps in quotes (alpha-10
 *                                 fix; ``stripSurroundingQuotes``).
 *   * ``file:///C:/Foo/Bar``    — drag-from-Explorer-into-browser-bar
 *                                 produces this on some browsers.
 *   * ``%5C%5Cserver%5Cshare``  — URL-encoded UNC paste.
 *   * trailing whitespace       — invisible newlines from terminal copy.
 *   * trailing path separator   — ``C:\Foo\Bar\`` confuses some Path
 *                                 resolvers; trim it.
 *   * mixed Unicode whitespace  — non-breaking space, em-space etc.
 *                                 from PDF / Word copy.
 *
 * This function applies all of them in order. Pure; no IO.
 *
 * Idempotency caveat (alpha-13 reviewer ticket): this function is
 * idempotent on every transformation EXCEPT percent-decoding. A
 * pathological input like ``%2520foo`` decodes to ``%20foo`` on the
 * first pass and to `` foo`` on the second. The current call sites
 * (``submitManualPath`` + ``classifyPathShape``) only invoke this once
 * per submit/classify off the raw user input, so the reflective
 * ``setManualPath(cleaned)`` pattern is safe in practice. A future
 * caller that loops on this function would be surprised; if you need
 * loop-safe idempotency, either gate the percent-decode on a
 * "did-this-already" sentinel or call it exactly once per user input.
 */
export function normalisePastedPath(raw: string): string {
  if (raw == null) return "";
  let p = raw;
  // 1. Strip Unicode whitespace at start/end (covers   NBSP,   EM SPACE, etc.)
  p = p.replace(/^[\s  -​  　]+/u, "");
  p = p.replace(/[\s  -​  　]+$/u, "");
  // 2. Strip surrounding ASCII double-quotes (Win+Shift+C).
  p = stripSurroundingQuotes(p);
  // 3. file:// URI -> filesystem path.
  //    file:///C:/Foo/Bar -> C:/Foo/Bar  (note the THREE slashes for absolute paths)
  //    file://server/share -> \\server\share (UNC)
  if (/^file:\/\//i.test(p)) {
    let withoutScheme = p.replace(/^file:\/\//i, "");
    // file:///C:/Foo -> drop the leading "/" before the drive letter.
    if (/^\/[A-Za-z]:/.test(withoutScheme)) {
      withoutScheme = withoutScheme.slice(1);
    } else if (withoutScheme.startsWith("/")) {
      // file:///foo -> /foo (POSIX absolute) — keep the leading slash.
      // No transformation needed.
    } else if (withoutScheme.length > 0) {
      // file://server/share -> \\server\share
      withoutScheme = "\\\\" + withoutScheme.replace(/\//g, "\\");
    }
    p = withoutScheme;
  }
  // 4. URL-decode percent-encoded sequences (handles dropped-from-browser cases).
  try {
    if (/%[0-9A-Fa-f]{2}/.test(p)) {
      p = decodeURIComponent(p);
    }
  } catch {
    // Malformed percent-encoding — leave as-is and let the backend 400.
  }
  // 5. Strip trailing path separator (but NOT for a single-character "C:\" or "/").
  if (p.length > 3 && /[\\/]$/.test(p)) {
    p = p.replace(/[\\/]+$/, "");
  }
  return p;
}

/**
 * Three-outcome client-side path classification for live-validation
 * feedback in the typed-path input. Lets the GUI render an inline hint
 * AS THE USER TYPES rather than waiting for them to submit and getting
 * a backend 400 round-trip.
 *
 * * ``empty``       — nothing typed yet; render placeholder, no error.
 * * ``looks_ok``    — passes ``looksAbsolute`` after normalisation; submit-eligible.
 * * ``not_absolute`` — non-empty but doesn't match Windows-drive / UNC / POSIX-absolute shape.
 *
 * Pure function.
 */
export type PathShape = "empty" | "looks_ok" | "not_absolute";

export function classifyPathShape(raw: string): PathShape {
  const normalised = normalisePastedPath(raw);
  if (!normalised) return "empty";
  return looksAbsolute(normalised) ? "looks_ok" : "not_absolute";
}
