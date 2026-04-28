"""Release gauntlet — pre-ship verification for the Meridian wheel + installer.

Run from the repo root:

    uv run python scripts/release_gauntlet.py

Exits 0 only when every step passes. Each step targets a bug class that has
already been shipped (and embarrassed us) on the v0.2.0-alpha line:

    1.  PowerShell parser check on installer/*.ps1 / *.psm1
    2.  ASCII-only check on installer/*.{ps1,psm1,bat,cmd}   (alpha-3 em-dash)
    2b. Installer URL constants — never 'http://localhost'   (alpha-5 IPv6/4)
    3.  Wheel build precondition (apps/web/out present) + wheel content check
    4.  Fresh-venv install of the just-built wheel
    5.  Cwd=System32 simulation — backend launched from a System32-like cwd
        must still write logs to MERIDIAN_HOME, not the cwd               (alpha-2)
    6.  /setup/ wizard URL probe — body contains "Meridian"               (alpha-3)
    7.  Version assertion — /health JSON.version == pyproject version    (alpha-1)
    7b. ODA-detection contract — scan response includes 'oda' object
        with 'available' (bool) and 'install_url' (str)                  (alpha-11)
    7c. Import status-pivot smoke — kick a tiny job, poll, confirm
        status reaches 'succeeded' and 'failed_groups' is a list. Catches
        the alpha-10 frontend-typed-it-wrong bug class                   (alpha-11)
    2c. Detach-pattern + .env-loader static check — installer/Start-
        Meridian.bat AND the Install-Meridian.ps1 inline copy contain
        pythonw.exe + WindowStyle Hidden (alpha-12 detach contract) AND
        MERIDIAN_ENV_FILE + Set-Item -Path "Env: (alpha-14 .env-loader
        contract). Statically guards the alpha-13 documentation-vs-
        reality gap that step 7h's subprocess-level test alone cannot
        catch -- a future commit that drops the .env loader from the
        .bat would pass 7h but ship the broken launcher.       (alpha-12 + alpha-14)
    2d. Launcher .bat exec-policy check — installer/Meridian-Console.bat
        invokes powershell.exe with -ExecutionPolicy Bypass. Catches
        regressions that re-expose the alpha-11 ExecutionPolicy block.   (alpha-12)
    7f. /setup/runtime contract — endpoint returns pid, started_at,
        uptime_seconds, version, python_version, platform. Catches
        regressions that break the operator triage path.                 (alpha-12)
    7g. Auth bypass default-secure — /setup/runtime.auth_disabled is
        false on a fresh install with no env var set. Catches a
        regression that defaults the alpha-13 MERIDIAN_AUTH_DISABLED
        bypass on.                                                       (alpha-13)
    7h. .env-loaded env-var contract — write MERIDIAN_AUTH_DISABLED=1
        into a temp .env, spawn the backend with the launcher's .env-
        loading semantics, and confirm /setup/runtime reports
        auth_disabled=true. Locks the contract ".env in the install
        dir -> env-var in the running backend -> /setup/runtime
        reflects it." Catches the alpha-13 documentation-vs-reality
        gap where Start-Meridian.bat never loaded .env.                  (alpha-14)
    8.  CLI --help smoke (no browser opens)
    9.  Summary

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

        # Step 7b — ODA-detection contract (alpha-11). A scan response
        # must include the new `oda` object with `available` (bool) and
        # `install_url` (str). Catches frontend-blocking schema drift.
        scan_target = scratch / "gauntlet_oda_probe"
        scan_target.mkdir(exist_ok=True)
        scan_payload = json.dumps({"folder_path": str(scan_target)}).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{base}/setup/import-folder/scan",
                data=scan_payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "release-gauntlet",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — localhost
                scan_body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            _fail("step 7b: ODA-detection contract", f"scan POST failed: {exc}")
            return False
        oda = scan_body.get("oda")
        if not isinstance(oda, dict):
            _fail(
                "step 7b: ODA-detection contract",
                "scan response missing the 'oda' object — alpha-11 regression class. "
                "Frontend cannot render the pre-import 'install ODA File Converter' "
                f"callout without it. Body keys: {sorted(scan_body)}",
            )
            return False
        for required in ("available", "install_url"):
            if required not in oda:
                _fail(
                    "step 7b: ODA-detection contract",
                    f"oda object missing required key '{required}'. oda={oda!r}",
                )
                return False
        if not isinstance(oda["available"], bool):
            _fail(
                "step 7b: ODA-detection contract",
                f"oda.available must be bool, got {type(oda['available']).__name__}",
            )
            return False
        if not (isinstance(oda["install_url"], str) and oda["install_url"].startswith("http")):
            _fail(
                "step 7b: ODA-detection contract",
                f"oda.install_url must be an http URL, got {oda['install_url']!r}",
            )
            return False
        _ok(
            "step 7b: ODA-detection contract",
            f"oda.available={oda['available']}, install_url present",
        )

        # Step 7c — folder-import status-pivot smoke (alpha-11). The
        # alpha-10 frontend pivoted on `status === "done"` while the
        # backend writes `"succeeded"`. This step kicks a tiny one-file
        # import and asserts the status terminal value matches the
        # canonical contract AND `failed_groups` is a list (the new
        # alpha-11 field). Without this step the frontend ↔ backend
        # contract drifts silently.
        import_target = scratch / "gauntlet_import_pivot"
        import_target.mkdir(exist_ok=True)
        # Empty folder yields total=0 which still finishes via the
        # 'succeeded' branch with imported=0. We exploit that — no need
        # to fabricate a real ingestable doc here.
        import_payload = json.dumps(
            {
                "folder_path": str(import_target),
                "project_name": "gauntlet-import-pivot",
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{base}/setup/import-folder",
                data=import_payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "release-gauntlet",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — localhost
                kick_body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            _fail("step 7c: import status-pivot smoke", f"kick POST failed: {exc}")
            return False
        job_id = kick_body.get("job_id")
        if not isinstance(job_id, str):
            _fail(
                "step 7c: import status-pivot smoke",
                f"import-folder response missing job_id; got {kick_body!r}",
            )
            return False
        # Poll until terminal — short deadline, the empty-folder path
        # finishes in milliseconds.
        deadline = time.monotonic() + 10.0
        last_body: dict = {}
        while time.monotonic() < deadline:
            poll_status, poll_payload = _probe(
                f"{base}/setup/import-folder/{job_id}", timeout=2.0
            )
            if poll_status == 200:
                try:
                    last_body = json.loads(poll_payload.decode("utf-8"))
                except json.JSONDecodeError:
                    last_body = {}
                if last_body.get("status") in ("succeeded", "failed"):
                    break
            time.sleep(0.1)
        terminal = last_body.get("status")
        if terminal not in ("succeeded", "failed"):
            _fail(
                "step 7c: import status-pivot smoke",
                f"job did not reach terminal status within deadline; last_body={last_body!r}",
            )
            return False
        if terminal != "succeeded":
            _fail(
                "step 7c: import status-pivot smoke",
                f"empty-folder import should yield status='succeeded', got '{terminal}'. "
                f"failed_groups={last_body.get('failed_groups')!r}",
            )
            return False
        if not isinstance(last_body.get("failed_groups"), list):
            _fail(
                "step 7c: import status-pivot smoke",
                "import-folder status response missing alpha-11 'failed_groups' list. "
                "Frontend's coalesced-error rendering depends on this field. "
                f"body keys: {sorted(last_body)}",
            )
            return False
        if not isinstance(last_body.get("oda"), dict):
            _fail(
                "step 7c: import status-pivot smoke",
                "import-folder status response missing alpha-11 'oda' object.",
            )
            return False
        _ok(
            "step 7c: import status-pivot smoke",
            f"job reached '{terminal}', failed_groups + oda present",
        )

        # Step 7f — /setup/runtime contract (alpha-12). Operator triage uses
        # this endpoint to answer pid / uptime / log path / last-job from
        # one HTTP call. Lock the field set so a future refactor doesn't
        # silently drop a key the launcher .bat / Status-Meridian.bat
        # depends on.
        rt_status, rt_payload = _probe(f"{base}/setup/runtime", timeout=5.0)
        if rt_status != 200:
            _fail(
                "step 7f: /setup/runtime contract",
                f"GET /setup/runtime returned {rt_status} (expected 200)",
            )
            return False
        try:
            rt_body = json.loads(rt_payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            _fail("step 7f: /setup/runtime contract", f"non-JSON body: {exc}")
            return False
        required_runtime = {
            "pid": int,
            "started_at": str,
            "uptime_seconds": (int, float),
            "version": str,
            "python_version": str,
            "platform": str,
        }
        for field, expected_type in required_runtime.items():
            if field not in rt_body:
                _fail(
                    "step 7f: /setup/runtime contract",
                    f"missing field {field!r}; got {sorted(rt_body)}",
                )
                return False
            if not isinstance(rt_body[field], expected_type):
                _fail(
                    "step 7f: /setup/runtime contract",
                    f"field {field!r} has type {type(rt_body[field]).__name__}, expected {expected_type}",
                )
                return False
        # Optional fields must be present even when null (Status-Meridian.bat reads them).
        for opt in ("backend_log_path", "structlog_dir", "last_import_job"):
            if opt not in rt_body:
                _fail(
                    "step 7f: /setup/runtime contract",
                    f"optional field {opt!r} missing entirely; must be present (may be null)",
                )
                return False
        _ok(
            "step 7f: /setup/runtime contract",
            f"pid={rt_body['pid']} version={rt_body['version']} uptime={rt_body['uptime_seconds']:.1f}s",
        )

        # Alpha-15: step 7g (auth bypass default-secure) and step 7h
        # (.env-loaded env-var contract) removed. They asserted on the
        # alpha-13/14 MERIDIAN_AUTH_DISABLED bypass machinery, which
        # alpha-15 retired by removing TOTP enforcement entirely
        # (validate-the-product-first principle).

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
# Step 7h — .env-loaded env-var contract (alpha-14)
# ──────────────────────────────────────────────────────────────────────────


def _parse_dotenv(text: str) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file. Mirrors the simple semantics
    Start-Meridian.bat (alpha-14) uses: ignore blank lines and lines
    starting with '#'; split on the first '='; strip surrounding quotes.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _step_env_var_via_dotenv(scratch: Path, venv_dir: Path, repo: Path) -> bool:
    """Step 7h (alpha-14): end-to-end verification that a `.env` file in
    MERIDIAN_HOME results in env vars reaching the running backend.

    Why this step exists: alpha-13 shipped MERIDIAN_AUTH_DISABLED=1 as an
    auth-bypass env var. The Python-side contract was tested. But the
    user-facing instructions ("Add MERIDIAN_AUTH_DISABLED=1 to
    C:\\Meridian\\.env, restart the backend") didn't actually work
    because Start-Meridian.bat never loaded .env. Alpha-14 fixed the
    launcher AND added this gauntlet step so the bug class never recurs.

    Simplification: we test the contract, NOT the .bat plumbing. The
    .bat plumbing is covered by the existing static checks (steps 2c/2d)
    plus alpha-14's e2e tests. Here we parse the .env in Python and
    pass the merged dict as `env=` to subprocess.Popen. That exercises
    the same OS-level "env reaches process -> /setup/runtime reports
    it" path from the perspective of contract correctness.
    """
    if os.name == "nt":
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"

    home_7h = scratch / "meridian_home_7h"
    home_7h.mkdir(exist_ok=True)
    spawn_cwd = scratch / "spawn_cwd_7h"
    spawn_cwd.mkdir(exist_ok=True)
    dotenv_path = home_7h / ".env"
    dotenv_path.write_text("MERIDIAN_AUTH_DISABLED=1\n", encoding="utf-8")

    parsed = _parse_dotenv(dotenv_path.read_text(encoding="utf-8"))
    if parsed.get("MERIDIAN_AUTH_DISABLED") != "1":
        _fail(
            "step 7h: env-var via .env contract",
            f".env parser failed to extract MERIDIAN_AUTH_DISABLED=1; got {parsed!r}",
        )
        return False

    port = 8001
    base = f"http://127.0.0.1:{port}"
    log_path = scratch / "backend_7h.log"

    env = {
        **os.environ,
        **parsed,
        "MERIDIAN_HOME": str(home_7h),
        "MERIDIAN_PORT": str(port),
        "MERIDIAN_HOST": "127.0.0.1",
        "MERIDIAN_BACKEND_LOG": str(log_path),
    }
    env.pop("PYTHONPATH", None)

    _info(
        f"step 7h: spawning backend on :{port} with .env-derived "
        f"MERIDIAN_AUTH_DISABLED={parsed.get('MERIDIAN_AUTH_DISABLED')!r}"
    )
    with log_path.open("wb") as logf:
        proc = subprocess.Popen(
            [str(py), "-m", "meridian.api.main"],
            cwd=str(spawn_cwd),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
    try:
        # 60s deadline @ 250ms intervals, per the brief.
        deadline = time.monotonic() + 60.0
        healthy = False
        while time.monotonic() < deadline:
            status, _body = _probe(f"{base}/health", timeout=0.5)
            if status == 200:
                healthy = True
                break
            time.sleep(0.25)
        if not healthy:
            try:
                tail = log_path.read_text(errors="replace").splitlines()[-30:]
            except OSError:
                tail = []
            _fail(
                "step 7h: env-var via .env contract",
                f"backend on :{port} never reported /health 200 within 60s. "
                "backend.log tail:\n" + "\n".join(tail),
            )
            return False

        rt_status, rt_payload = _probe(f"{base}/setup/runtime", timeout=5.0)
        if rt_status != 200:
            _fail(
                "step 7h: env-var via .env contract",
                f"GET /setup/runtime returned {rt_status} (expected 200)",
            )
            return False
        try:
            rt_body = json.loads(rt_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(
                "step 7h: env-var via .env contract",
                f"non-JSON body from /setup/runtime: {exc}",
            )
            return False
        auth_disabled = rt_body.get("auth_disabled")
        if not isinstance(auth_disabled, bool):
            _fail(
                "step 7h: env-var via .env contract",
                f"auth_disabled must be bool, got {type(auth_disabled).__name__}",
            )
            return False
        if auth_disabled is not True:
            _fail(
                "step 7h: env-var via .env contract",
                "auth_disabled=false on a backend spawned with "
                "MERIDIAN_AUTH_DISABLED=1 derived from .env. The .env -> env-var "
                "-> /setup/runtime contract is broken. This is the alpha-13 "
                "bug class -- the documentation said 'put it in .env' but "
                "the env var never reached the process. "
                f"rt_body keys: {sorted(rt_body)}",
            )
            return False
        _ok(
            "step 7h: env-var via .env contract",
            "auth_disabled=true after .env load",
        )
        return True
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        try:
            dotenv_path.unlink()
        except OSError:
            pass


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
# Step 2b — installer URL constants
# ──────────────────────────────────────────────────────────────────────────


def _step_detach_pattern(installer_dir: Path) -> bool:
    """Step 7d (alpha-12): the backend launch path MUST use pythonw.exe AND
    WindowStyle Hidden. Catches a regression that re-introduces the
    alpha-11 'close terminal kills backend' bug class.

    Two surfaces are checked:

      * ``installer/Start-Meridian.bat`` — the user-facing launcher.
        Must reference ``pythonw.exe`` AND ``-WindowStyle Hidden``.
      * ``installer/Install-Meridian.ps1`` — the install-time backend
        spawn. Must reference ``pythonw.exe`` AND ``-WindowStyle Hidden``
        in the ``Start-BackendAndOpenBrowser`` function.

    Both are static-content greps, not live-process tests — runtime
    detachment-survival belongs in tests/e2e where we can simulate
    process-group semantics deterministically.
    """
    start_bat = installer_dir / "Start-Meridian.bat"
    install_ps1 = installer_dir / "Install-Meridian.ps1"

    failures: list[str] = []
    # Alpha-14 reviewer-pull-forward: the alpha-13 documentation-vs-reality
    # gap (Start-Meridian.bat didn't load .env, so MERIDIAN_AUTH_DISABLED
    # in .env was orphaned) MUST be statically guarded. Without these
    # tokens, a regression that deletes the .env loader would pass step
    # 7h (subprocess-level) but ship a broken .bat. This closes the
    # alpha-13-class miss the rest of alpha-14 exists to prevent.
    required_tokens = (
        ("pythonw.exe",          "alpha-12 detach pattern (visible-cmd regression)"),
        ("WindowStyle Hidden",   "alpha-12 detach pattern (visible-cmd regression)"),
        # Alpha-15: dropped MERIDIAN_ENV_FILE / Set-Item Env: tokens that
        # alpha-14 added. Those existed only to wire the alpha-13
        # MERIDIAN_AUTH_DISABLED bypass through .env -- which alpha-15
        # retired by removing TOTP enforcement entirely. The cmd.exe
        # parser pathology in alpha-14's multi-line PS argument is also
        # gone (single-line spawn restored). Validate-the-product-first.
    )

    if not start_bat.exists():
        failures.append(f"missing: {start_bat}")
    else:
        text = start_bat.read_text(encoding="utf-8", errors="replace")
        for token, why in required_tokens:
            if token not in text:
                failures.append(f"{start_bat.name}: missing {token!r} -- {why}")

    if not install_ps1.exists():
        failures.append(f"missing: {install_ps1}")
    else:
        ps_text = install_ps1.read_text(encoding="utf-8", errors="replace")
        for token, why in required_tokens:
            if token not in ps_text:
                failures.append(f"{install_ps1.name}: missing {token!r} -- {why}")

    if failures:
        _fail(
            "step 2c: detach-pattern static check",
            "alpha-12 contract -- pythonw.exe + WindowStyle Hidden must appear in "
            "BOTH Start-Meridian.bat and the Install-Meridian.ps1 inline copy:\n  - "
            + "\n  - ".join(failures),
        )
        return False
    _ok(
        "step 2c: detach-pattern static check",
        "pythonw.exe + WindowStyle Hidden present in both surfaces",
    )
    return True


def _step_launcher_exec_policy(installer_dir: Path) -> bool:
    """Step 2d (alpha-12, broadened alpha-13): every .bat under installer/
    that invokes a .ps1 must use ``-ExecutionPolicy Bypass``.

    Alpha-12 only checked Meridian-Console.bat. Alpha-13 broadens the
    rule because a regression that adds a new .bat-invokes-.ps1 surface
    (Update-Meridian.bat, Repair-Meridian.bat, etc.) would silently
    re-expose the alpha-11 ExecutionPolicy block.

    Detection: any line in any .bat file that mentions ``powershell.exe``
    or ``powershell `` AND ``-File`` (i.e. running a script) must also
    contain ``-ExecutionPolicy Bypass``. Inline ``-Command`` invocations
    are exempt because PS execution policy doesn't gate them.
    """
    failures: list[str] = []
    bats = sorted(installer_dir.glob("*.bat"))
    if not bats:
        _info("No .bat files under installer/ -- nothing to check.")
        _ok("step 2d: launcher exec-policy (no bats present)")
        return True

    for bat in bats:
        try:
            lines = bat.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            failures.append(f"{bat.name}: could not read ({exc})")
            continue
        for line_num, raw in enumerate(lines, start=1):
            stripped = raw.lstrip()
            if stripped.startswith("REM ") or stripped.startswith("::"):
                continue
            lower = stripped.lower()
            invokes_ps = (
                "powershell.exe" in lower or
                lower.startswith("powershell ") or
                " powershell " in lower
            )
            if not invokes_ps:
                continue
            # Only flag invocations that run a .ps1 script (-File). Inline
            # -Command / -EncodedCommand / -NoProfile-only invocations
            # don't hit the script-execution policy gate.
            if "-file" not in lower:
                continue
            if "-executionpolicy bypass" not in lower:
                failures.append(
                    f"{bat.name}:{line_num}: invokes a .ps1 via -File but lacks "
                    "-ExecutionPolicy Bypass"
                )
    if failures:
        _fail(
            "step 2d: launcher exec-policy",
            "every .bat invoking a .ps1 via -File must include "
            "-ExecutionPolicy Bypass (alpha-13 broadening of the alpha-12 "
            "Meridian-Console-only check):\n  - " + "\n  - ".join(failures),
        )
        return False
    _ok(
        "step 2d: launcher exec-policy",
        f"{len(bats)} .bat file(s) under installer/ all bypass ExecutionPolicy where needed",
    )
    return True


def _step_installer_url_constants(installer_dir: Path) -> bool:
    """Catches the alpha-5 regression class: Install-Meridian.ps1 must NOT
    use 'http://localhost:...' in URL constants.

    On Windows, the name 'localhost' resolves to ``::1`` (IPv6 loopback)
    before falling back to ``127.0.0.1`` (IPv4). Uvicorn binds IPv4 only by
    default. PowerShell's HttpWebRequest with a 1s timeout doesn't fall
    back to IPv4 fast enough — the probe targets an empty IPv6 socket,
    times out, and the installer hangs forever at 'Waiting for the backend'
    even though the backend is fully healthy. The user lived this in alpha-4.

    All probe / open URL constants in the installer must use ``127.0.0.1``
    explicitly. Browsers handle IPv6→IPv4 fallback well; PowerShell does not.
    """
    bad: list[str] = []
    for ps1 in installer_dir.rglob("*.ps1"):
        for line_num, raw in enumerate(ps1.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if line.startswith("#"):
                continue  # tolerate localhost in comments / docstrings
            if "://localhost" in line:
                bad.append(f"  {ps1.name}:{line_num}: {line}")
    if bad:
        _fail(
            "step 2b: installer URL constants",
            "found 'http://localhost' URL(s) in installer .ps1 files. Use 127.0.0.1 "
            "— Windows resolves localhost to IPv6 ::1 first; uvicorn binds IPv4. "
            "Probes hang forever.\n" + "\n".join(bad),
        )
        return False
    _ok("step 2b: installer URL constants", "no 'http://localhost' URLs in installer .ps1 files")
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
    if not _step_installer_url_constants(installer_dir):
        return 1
    if not _step_detach_pattern(installer_dir):
        return 1
    if not _step_launcher_exec_policy(installer_dir):
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

        # Alpha-15: step 7h removed; tested the .env -> bypass plumbing
        # which alpha-15 retired with TOTP enforcement.

        if not _step_cli_help_smoke(venv_dir):
            return 1

    print()
    print("[ ALL ] release gauntlet PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
