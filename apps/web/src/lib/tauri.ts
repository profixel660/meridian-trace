/**
 * Tauri bridge for the desktop shell.
 *
 * When the web UI is bundled inside the Tauri shell (Stream A), the
 * `__TAURI__` global is injected and the @tauri-apps/plugin-* modules are
 * available. When running under `npm run dev` in a plain browser, the
 * plugin imports must be lazy (so Next's bundler doesn't try to resolve
 * them at build time on a machine that doesn't have them) and the
 * fallbacks degrade to standard browser APIs.
 *
 * The wizard prose (see `copy.ts`) flags the limitations of browser-mode
 * file pickers — `<input type="file">` returns blob URLs, not real local
 * paths, so first-documents import only works inside the Tauri shell.
 */

declare global {
  interface Window {
    __TAURI__?: unknown;
  }
}

/** Synchronous predicate — true only when running inside the Tauri shell. */
export function isInTauri(): boolean {
  return (
    typeof globalThis !== "undefined" &&
    typeof (globalThis as { __TAURI__?: unknown }).__TAURI__ !== "undefined"
  );
}

/**
 * Open a native folder-picker (Tauri) or a browser `webkitdirectory`
 * input (fallback).
 *
 * Returns the absolute folder path inside Tauri, or `null` when the user
 * cancels. In browser fallback mode the absolute path is unavailable
 * (security restriction); we return `null` so the wizard surfaces the
 * "browser mode can't pick real folders — open the desktop app" guidance.
 */
export async function pickFolder(opts?: {
  defaultPath?: string;
  title?: string;
}): Promise<string | null> {
  if (isInTauri()) {
    try {
      const mod = (await import("@tauri-apps/plugin-dialog")) as {
        open: (o: unknown) => Promise<unknown>;
      };
      const result = await mod.open({
        directory: true,
        multiple: false,
        defaultPath: opts?.defaultPath,
        title: opts?.title ?? "Choose a folder",
      });
      if (typeof result === "string") return result;
      return null;
    } catch (err) {
      // Plugin missing or call failed — log and fall through to fallback.
      // eslint-disable-next-line no-console
      console.warn("[tauri.pickFolder] native picker failed", err);
      return null;
    }
  }
  // Browser fallback: trigger a hidden <input type="file" webkitdirectory>.
  // The browser hides the absolute path; this returns null so the caller
  // can show the "browser mode unsupported" message.
  return new Promise((resolve) => {
    if (typeof document === "undefined") {
      resolve(null);
      return;
    }
    const input = document.createElement("input");
    input.type = "file";
    // `webkitdirectory` is non-standard but supported by Chromium-based browsers.
    input.setAttribute("webkitdirectory", "");
    input.setAttribute("directory", "");
    input.style.display = "none";
    input.onchange = () => {
      // Browsers expose only the directory NAME via webkitRelativePath, not
      // the parent path. We can't recover an absolute path. Return null and
      // let the wizard message do the talking.
      document.body.removeChild(input);
      resolve(null);
    };
    input.oncancel = () => {
      document.body.removeChild(input);
      resolve(null);
    };
    document.body.appendChild(input);
    input.click();
  });
}

/**
 * Open a native multi-file picker (Tauri) or a browser `<input type="file">`
 * (fallback). In Tauri the returned strings are absolute disk paths; in
 * the browser fallback the array will be empty (browser can't expose real
 * paths) and the wizard surfaces the limitation.
 */
export async function pickFiles(opts?: {
  extensions?: string[];
  multiple?: boolean;
  title?: string;
}): Promise<string[]> {
  if (isInTauri()) {
    try {
      const mod = (await import("@tauri-apps/plugin-dialog")) as {
        open: (o: unknown) => Promise<unknown>;
      };
      const result = await mod.open({
        multiple: opts?.multiple ?? true,
        directory: false,
        title: opts?.title ?? "Choose files",
        filters: opts?.extensions
          ? [{ name: "Documents", extensions: opts.extensions }]
          : undefined,
      });
      if (!result) return [];
      if (Array.isArray(result)) {
        return result.filter((x): x is string => typeof x === "string");
      }
      if (typeof result === "string") return [result];
      return [];
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[tauri.pickFiles] native picker failed", err);
      return [];
    }
  }
  // Browser fallback — returns nothing because absolute paths aren't
  // available; the wizard tells the user to use the desktop shell.
  return [];
}

/**
 * Open a URL in the user's default external browser. Inside Tauri this
 * routes through @tauri-apps/plugin-shell so links don't open inside the
 * app's webview; in the browser we fall back to `window.open`.
 */
export async function openExternal(url: string): Promise<void> {
  if (isInTauri()) {
    try {
      const mod = (await import("@tauri-apps/plugin-shell")) as {
        open: (u: string) => Promise<void>;
      };
      await mod.open(url);
      return;
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[tauri.openExternal] native shell failed", err);
      // Fall through to browser behaviour.
    }
  }
  if (typeof window !== "undefined") {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
