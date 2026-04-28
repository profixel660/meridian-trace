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
