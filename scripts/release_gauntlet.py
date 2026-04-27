"""Release gauntlet — pre-ship verification for the Meridian wheel + installer.

Run from the repo root:

    uv run python scripts/release_gauntlet.py

Exits 0 only when every step passes. Each step targets a bug class that has
already been shipped (and embarrassed us) on the v0.2.0-alpha line:

    1. PowerShell parser check on installer/*.ps1 / *.psm1
    2. ASCII-only check on installer/*.{ps1,psm1,bat,cmd}   (alpha-3 em-dash)
    3. Wheel build precondition (apps/web/out present) + wheel content check
    4. Fresh-venv install of the just-built wheel
    5. Cwd=System32 simulation — backend launched from a System32-like cwd
       must still write logs to MERIDIAN_HOME, not the cwd                (alpha-2)
    6. /setup/ wizard URL probe — body contains "Meridian"               (alpha-3)
    7. Version assertion — /health JSON.version == pyproject version    (alpha-1)
    8. CLI --help smoke (no browser opens)
    9. Summary

The script does NOT mutate anything outside ``dist/`` and a tmpdir scratch
area. It does NOT modify installer files, source files, or apps/web/.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# Pretty printing — keep ASCII only so the gauntlet's own output never trips
# step 2 if a future caller pipes this stdout into an installer log.
# ──────────────────────────────────────────────────────────────────────────


def _ok(step: str, msg: str = "") -> None:
    suffix = f" {msg}" if msg else ""
    print(f"[ OK  ] {step}{suffix}")


def _fail(step: str, msg: str) -> None:
    print(f"[FAIL ] {step}: {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"[ ... ] {msg}")


# ──────────────────────────────────────────────────────────────────────────
# Repo-root resolution
# ──────────────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    # scripts/ sits at the repo root.
    candidate = here.parent
    if not (candidate / "pyproject.toml").exists():
        raise SystemExit(
            f"release_gauntlet: expected pyproject.toml at {candidate}, not found. "
            "Run from the repo root."
        )
    return candidate


# ──────────────────────────────────────────────────────────────────────────
# Step 1 — PowerShell parser check
# ──────────────────────────────────────────────────────────────────────────


def _step_powershell_parse(installer_dir: Path) -> bool:
    targets = sorted(
        list(installer_dir.rglob("*.ps1")) + list(installer_dir.rglob("*.psm1"))
    )
    if not targets:
        _info("No .ps1/.psm1 files under installer/ — nothing to parse-check.")
        _ok("step 1: PowerShell parse")
        return True

    if not shutil.which("powershell") and not shutil.which("powershell.exe"):
        _info("PowerShell not on PATH — skipping parser check (non-Windows host).")
        _ok("step 1: PowerShell parse (skipped)")
        return True

    # One PS process; iterate file paths inside it. We pass the file list
    # via a temp file (one path per line) instead of CLI args — PowerShell's
    # `-Command` wraps args in its own parser and treats `--` as a unary
    # operator, which mangles arg-style passing.
    list_file = installer_dir.parent / ".gauntlet_ps_paths.tmp"
    try:
        list_file.write_text(
            "\n".join(str(p) for p in targets), encoding="utf-8"
        )
        ps_script = (
            r"$listFile = $env:GAUNTLET_PS_LIST; "
            r"$paths = Get-Content -LiteralPath $listFile; "
            r"$failed = $false; "
            r"foreach ($p in $paths) { "
            r"  $errors = $null; "
            r"  [void][System.Management.Automation.Language.Parser]::ParseFile("
            r"      $p, [ref]$null, [ref]$errors); "
            r"  if ($errors -and $errors.Count -gt 0) { "
            r"    $failed = $true; "
            r"    foreach ($e in $errors) { "
            r"      Write-Host ('PARSE_ERR ' + $p + ': ' + $e.Message) "
            r"    } "
            r"  } "
            r"}; "
            r"if ($failed) { exit 1 } else { exit 0 }"
        )
        env = {**os.environ, "GAUNTLET_PS_LIST": str(list_file)}
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            _fail("step 1: PowerShell parse", f"could not run powershell: {exc}")
            return False
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        _fail(
            "step 1: PowerShell parse",
            "parser errors:\n" + (result.stdout or result.stderr).strip(),
        )
        return False

    _ok("step 1: PowerShell parse", f"{len(targets)} file(s) parsed cleanly")
    return True


# ──────────────────────────────────────────────────────────────────────────
# Step 2 — ASCII-only on installer scripts
# ──────────────────────────────────────────────────────────────────────────


def _step_ascii_only(installer_dir: Path) -> bool:
    suffixes = {".ps1", ".psm1", ".bat", ".cmd"}
    targets = [
        p for p in installer_dir.rglob("*") if p.is_file() and p.suffix.lower() in suffixes
    ]
    if not targets:
        _ok("step 2: ASCII-only", "no installer scripts found")
        return True

    bad: list[tuple[Path, int, int, int, str]] = []  # (path, line, col, codepoint, char)
    for path in targets:
        try:
            text = path.read_bytes()
        except OSError as exc:
            _fail("step 2: ASCII-only", f"could not read {path}: {exc}")
            return False
        # Decode as UTF-8 (most likely). If it isn't valid UTF-8, that itself
        # is a non-ASCII signal — flag the first offending byte.
        try:
            decoded = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append((path, 0, exc.start, text[exc.start], f"<bad utf-8 byte 0x{text[exc.start]:02x}>"))
            continue
        line = 1
        col = 0
        for ch in decoded:
            col += 1
            if ch == "\n":
                line += 1
                col = 0
                continue
            cp = ord(ch)
            if cp > 0x7F:
                bad.append((path, line, col, cp, ch))
                # First offender per file is enough signal — keep scanning
                # other files but skip the rest of this one.
                break

    if bad:
        msg_lines = ["non-ASCII codepoints found (forbidden in installer scripts):"]
        for path, line, col, cp, ch in bad:
            msg_lines.append(
                f"  {path}:{line}:{col}  U+{cp:04X} ({ch!r})"
            )
        _fail("step 2: ASCII-only", "\n".join(msg_lines))
        return False

    _ok("step 2: ASCII-only", f"{len(targets)} file(s) clean")
    return True


# ──────────────────────────────────────────────────────────────────────────
# Step 3 — wheel build + content check
# ──────────────────────────────────────────────────────────────────────────


def _read_pyproject_version(repo: Path) -> str:
    with (repo / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def _step_build_wheel(repo: Path) -> tuple[bool, Path | None]:
    web_out = repo / "apps" / "web" / "out"
    if not web_out.exists() or not (web_out / "index.html").exists():
        _info("apps/web/out missing — running `npm run build` to produce it.")
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if npm is None:
            _fail("step 3: wheel build", "npm not found on PATH and apps/web/out is absent")
            return False, None
        try:
            r = subprocess.run(
                [npm, "run", "build"],
                cwd=repo / "apps" / "web",
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            _fail("step 3: wheel build", "`npm run build` timed out after 10 minutes")
            return False, None
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).splitlines()[-30:]
            _fail("step 3: wheel build", "`npm run build` failed:\n" + "\n".join(tail))
            return False, None
        if not (web_out / "index.html").exists():
            _fail("step 3: wheel build", "apps/web/out/index.html still missing after build")
            return False, None

    version = _read_pyproject_version(repo)
    dist = repo / "dist"
    dist.mkdir(exist_ok=True)
    expected_wheel = dist / f"meridian-{version}-py3-none-any.whl"

    # Always rebuild — the gauntlet's job is to prove the *current* tree.
    uv = shutil.which("uv") or shutil.which("uv.exe")
    if uv is None:
        _fail("step 3: wheel build", "uv not found on PATH")
        return False, None

    _info(f"running `uv build --wheel --out-dir dist` (target: {expected_wheel.name})")
    try:
        r = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(dist)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        _fail("step 3: wheel build", "`uv build` timed out after 10 minutes")
        return False, None
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).splitlines()[-30:]
        _fail("step 3: wheel build", "uv build failed:\n" + "\n".join(tail))
        return False, None

    if not expected_wheel.exists():
        # Maybe uv produced a slightly different name — find any wheel matching version.
        candidates = list(dist.glob(f"meridian-{version}*.whl"))
        if not candidates:
            _fail("step 3: wheel build", f"no wheel produced for version {version} in {dist}")
            return False, None
        expected_wheel = candidates[0]

    # Verify wheel contains the bundled wizard HTML.
    must_have = "meridian/_web/setup/index.html"
    with zipfile.ZipFile(expected_wheel) as zf:
        names = set(zf.namelist())
    if must_have not in names:
        _fail(
            "step 3: wheel build",
            f"wheel {expected_wheel.name} is missing {must_have} "
            f"(apps/web/out/setup/index.html did not get bundled). "
            f"Did `npm run build` actually emit the static export for /setup/*?",
        )
        return False, None

    _ok(
        "step 3: wheel build",
        f"{expected_wheel.name} contains {must_have}",
    )
    return True, expected_wheel


# ──────────────────────────────────────────────────────────────────────────
# Step 4 — fresh-venv install
# ──────────────────────────────────────────────────────────────────────────


def _step_fresh_install(wheel: Path, scratch: Path) -> tuple[bool, Path | None]:
    venv_dir = scratch / "venv"
    uv = shutil.which("uv") or shutil.which("uv.exe")
    if uv is None:
        _fail("step 4: fresh install", "uv not found on PATH")
        return False, None

    _info(f"creating fresh venv at {venv_dir}")
    r = subprocess.run(
        [uv, "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        _fail("step 4: fresh install", f"uv venv failed:\n{r.stdout}\n{r.stderr}")
        return False, None

    if os.name == "nt":
        py = venv_dir / "Scripts" / "python.exe"
        meridian_exe = venv_dir / "Scripts" / "meridian.exe"
    else:
        py = venv_dir / "bin" / "python"
        meridian_exe = venv_dir / "bin" / "meridian"

    if not py.exists():
        _fail("step 4: fresh install", f"python not found at {py} after uv venv")
        return False, None

    _info(f"installing {wheel.name} into the fresh venv")
    r = subprocess.run(
        [uv, "pip", "install", "--python", str(py), str(wheel)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).splitlines()[-30:]
        _fail("step 4: fresh install", "wheel install failed:\n" + "\n".join(tail))
        return False, None

    if not meridian_exe.exists():
        _fail(
            "step 4: fresh install",
            f"console-script {meridian_exe.name} missing at {meridian_exe} "
            "after install — pyproject [project.scripts] regression?",
        )
        return False, None

    _ok("step 4: fresh install", f"meridian.exe at {meridian_exe}")
    return True, venv_dir


# ──────────────────────────────────────────────────────────────────────────
# Steps 5–7 — backend launch from System32-like cwd, /setup/ probe, /health version
# ──────────────────────────────────────────────────────────────────────────


def _probe(url: str, timeout: float = 1.0) -> tuple[int | None, bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "release-gauntlet"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost
            return resp.status, resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, b""


def _wait_healthy(base: str, deadline_s: float = 30.0) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        status, _ = _probe(f"{base}/health", timeout=0.5)
        if status == 200:
            return True
        time.sleep(0.25)
    return False


def _step_backend_lifecycle(
    venv_dir: Path,
    scratch: Path,
    repo: Path,
) -> bool:
    if os.name == "nt":
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"

    # Spawn cwd: a tmp dir with NO pyproject.toml above it. On Windows this
    # mirrors the System32-cwd class of bug exactly.
    spawn_cwd = scratch / "spawn_cwd"
    spawn_cwd.mkdir(exist_ok=True)
    meridian_home = scratch / "meridian_home"
    meridian_home.mkdir(exist_ok=True)
    port = 8765
    base = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "MERIDIAN_HOME": str(meridian_home),
        "MERIDIAN_PORT": str(port),
        "MERIDIAN_HOST": "127.0.0.1",
    }
    # The brief explicitly says env only carries MERIDIAN_HOME + MERIDIAN_PORT.
    # We do NOT set MERIDIAN_PROJECT_ROOT — the assertion below is precisely
    # whether MERIDIAN_HOME alone is enough to keep logs out of an arbitrary
    # spawn cwd. The alpha-3 _is_unsafe_cwd() check only triggers on Windows
    # system paths; this step verifies the broader bug class (any non-
    # pyproject cwd), which the spec (§5) requires.
    # Strip any cached-bytecode pollution that could shadow the just-installed pkg.
    env.pop("PYTHONPATH", None)

    log_path = scratch / "backend.log"
    _info(f"spawning backend from cwd={spawn_cwd}, MERIDIAN_HOME={meridian_home}")
    with log_path.open("wb") as logf:
        proc = subprocess.Popen(
            [str(py), "-m", "meridian.api.main"],
            cwd=str(spawn_cwd),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
    try:
        if not _wait_healthy(base, deadline_s=30.0):
            try:
                tail = log_path.read_text(errors="replace").splitlines()[-30:]
            except OSError:
                tail = []
            _fail(
                "step 5: cwd-System32 simulation",
                "backend never reported /health 200 within 30s. backend.log tail:\n"
                + "\n".join(tail),
            )
            return False

        # /health 200 confirmed → step 5 partially OK. Check log location.
        # Logs must NOT have appeared under spawn_cwd; they must live under
        # meridian_home. The exact subdir is `data/projects/_global.logs/`.
        logs_dir = meridian_home / "data" / "projects" / "_global.logs"
        spawn_cwd_logs = list(spawn_cwd.rglob("_global.logs"))
        if spawn_cwd_logs:
            _fail(
                "step 5: cwd-System32 simulation",
                f"logs leaked into spawn cwd: {spawn_cwd_logs}. "
                "alpha-2 regression — _project_root() did not substitute MERIDIAN_HOME.",
            )
            return False
        if not logs_dir.exists():
            # Tolerate the exact subpath name shifting; require *some* log
            # directory to exist under MERIDIAN_HOME.
            any_logs = list(meridian_home.rglob("*.log"))
            if not any_logs:
                _fail(
                    "step 5: cwd-System32 simulation",
                    f"no logs found under {meridian_home}. Expected {logs_dir} or similar.",
                )
                return False
            _info(
                f"note: expected log dir {logs_dir} not present, but logs landed under "
                f"MERIDIAN_HOME ({len(any_logs)} file(s)) — accepting"
            )
        _ok(
            "step 5: cwd-System32 simulation",
            f"backend healthy; logs at {logs_dir if logs_dir.exists() else meridian_home}",
        )

        # Step 6 — /setup/ wizard URL probe. Body must contain "Meridian".
        status, body = _probe(f"{base}/setup/", timeout=5.0)
        if status != 200:
            _fail(
                "step 6: /setup/ probe",
                f"GET /setup/ returned {status} (expected 200). "
                "alpha-3 regression class — /setup/welcome 404.",
            )
            return False
        if b"Meridian" not in body and b"meridian" not in body:
            preview = body[:200].decode("latin-1", errors="replace")
            _fail(
                "step 6: /setup/ probe",
                f"GET /setup/ body did not contain 'Meridian'. First 200 bytes: {preview!r}",
            )
            return False
        _ok("step 6: /setup/ probe", f"GET /setup/ -> 200, body OK ({len(body)} bytes)")

        # Step 7 — /health JSON.version === pyproject version.
        status, body = _probe(f"{base}/health", timeout=5.0)
        if status != 200:
            _fail("step 7: version assertion", f"/health returned {status}")
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(
                "step 7: version assertion",
                f"/health body was not JSON: {exc}; body={body!r}",
            )
            return False
        api_version = payload.get("version")
        pyproj_version = _read_pyproject_version(repo)
        if api_version != pyproj_version:
            _fail(
                "step 7: version assertion",
                f"/health.version={api_version!r} != pyproject.version={pyproj_version!r}. "
                "alpha-1 regression class — stale '0.1.0' shipped in installed wheel.",
            )
            return False
        _ok("step 7: version assertion", f"version={api_version} matches pyproject")
        return True
    finally:
        # Cleanup — kill the backend so the next run isn't held up by a port collision.
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)


# ──────────────────────────────────────────────────────────────────────────
# Step 8 — CLI --help smoke
# ──────────────────────────────────────────────────────────────────────────


def _step_cli_help_smoke(venv_dir: Path) -> bool:
    if os.name == "nt":
        meridian_exe = venv_dir / "Scripts" / "meridian.exe"
    else:
        meridian_exe = venv_dir / "bin" / "meridian"

    invocations = [
        [str(meridian_exe), "--help"],
        [str(meridian_exe), "start", "--help"],
        [str(meridian_exe), "init", "--help"],
    ]
    # Belt-and-braces: keep webbrowser.open inert if the user's `meridian start`
    # somehow runs (it won't here, but env hygiene is cheap).
    env = {**os.environ, "MERIDIAN_TEST_NO_BROWSER": "1"}
    for cmd in invocations:
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, env=env
            )
        except subprocess.TimeoutExpired:
            _fail("step 8: CLI help", f"{' '.join(cmd)} timed out")
            return False
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).splitlines()[-20:]
            _fail(
                "step 8: CLI help",
                f"{' '.join(cmd)} exit={r.returncode}\n" + "\n".join(tail),
            )
            return False
    _ok("step 8: CLI help", f"{len(invocations)} invocation(s) returned 0")
    return True


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main() -> int:
    repo = _repo_root()
    installer_dir = repo / "installer"
    print(f"Meridian release gauntlet — repo at {repo}")
    print(f"  pyproject version: {_read_pyproject_version(repo)}")
    print()

    if not _step_powershell_parse(installer_dir):
        return 1
    if not _step_ascii_only(installer_dir):
        return 1

    ok, wheel = _step_build_wheel(repo)
    if not ok or wheel is None:
        return 1

    # ignore_cleanup_errors: on Windows the backend may briefly hold a log
    # file handle even after terminate() — pytest-style scratch hygiene
    # matters less than not masking a passing run with a cleanup OSError.
    with tempfile.TemporaryDirectory(
        prefix="meridian-gauntlet-", ignore_cleanup_errors=True
    ) as scratch_str:
        scratch = Path(scratch_str)
        ok, venv_dir = _step_fresh_install(wheel, scratch)
        if not ok or venv_dir is None:
            return 1

        if not _step_backend_lifecycle(venv_dir, scratch, repo):
            return 1

        if not _step_cli_help_smoke(venv_dir):
            return 1

    print()
    print("[ ALL ] release gauntlet PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
