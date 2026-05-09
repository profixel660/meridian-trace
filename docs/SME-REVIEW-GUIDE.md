# SME review guide

You have helped to guide the design of this tool up to and including alpha-23, and your testing each round is what tells us whether a given revision is fit to put in front of a project manager. This guide is the script for one round of testing: how to wipe the previous install, how to put the new one on, what to look at on this revision specifically, and what to send back.

If anything in this guide is unclear, write a note in your feedback document — clarity gaps in this guide are bugs in this guide, not in you.

## What you do each round

Every alpha revision follows the same shape:

1. **Wipe the previous install** so you're testing a clean state, not a half-upgraded one.
2. **Install the new revision** by downloading two files from the latest release and running them.
3. **Run the targeted checks for this revision** — listed under "Round-specific checks" below. These change every round.
4. **Send back your feedback document, screenshots, and the backend log** so the developer can correlate what you saw with what the tool actually did.

The total round usually takes 30–60 minutes of attended time, plus whatever the import takes to run (sometimes overnight on a large project folder).

## Step 1 — Wipe the previous install

You only need to do this if a previous revision is already installed on this machine.

1. Each alpha revision has a repository of associated files. When a new revision is published, you need to cleanse the target install PC of the last revision before testing the new one.
2. Go to the **Slack `#meridian-trace`** project channel.
3. On the right-hand panel of the window, on the channel home screen, you'll see a **Releases** section — click on the **latest release**. This opens the release page on GitHub.
4. Scroll down to the list of files under **Assets** and double-click the file named **`Reset-Meridian.ps1`**. The file will appear in your Downloads folder.
5. In Downloads, right-click the file and choose **Run as administrator** from the menu.
6. At the prompt, click **Open** to open the file in PowerShell.
7. A security warning will appear — type **`R`** to run once, then press **Enter**.
8. A warning will ask if you want to make changes to your computer — click **Yes**.
9. The next prompt will ask you to confirm wiping everything — type **`Y`** then press **Enter**.
10. When it finishes, press **Enter** to exit.

The PC is now clean and ready for the new install.

## Step 2 — Install the new revision

1. From the same release page (Slack → latest release → Assets), download both **`Install-Meridian.bat`** and **`Install-Meridian.ps1`**. Both files need to be in your Downloads folder before you start.
2. Right-click **`Install-Meridian.bat`** and choose **Run as administrator**.
3. The installer downloads Python (if missing), creates a virtual environment, downloads the latest Meridian wheel from the release, and prompts you for an Anthropic API key. Total time is usually 5–15 minutes, mostly downloading.
4. When the install finishes, you can delete the two installer files from Downloads — Meridian is now installed under `C:\Meridian` and runs in the background.
5. The web interface opens in your default browser. Set up your project there (you'll be asked for the API key again — this is normal in the alpha and will be consolidated later).

> **Note:** Once the tool is out of alpha, the install will be a single bundled installer, and the API key will be asked for once instead of twice.

## Step 3 — Round-specific checks

This section changes every round. **Do these checks in order**, and for each one, screenshot what you see and add a short note (good, bad, or weird) to your feedback document.

### alpha-23 — verify the "unclassified failed" bug from 02/05 is gone

The 02/05 round of alpha-22 testing surfaced a bug where the post-import banner reported **"127 files failed for an unclassified reason"** even though those files had actually imported successfully. Alpha-23 is the fix for that bug.

#### Targeted check (5 minutes of attended time + the import run-time)

1. Use the **same project folder** you used on 02/05 (the 347-file folder) so the comparison is apples-to-apples.
2. Go through the wizard normally — pick the folder, confirm the project name, hit Import.
3. Wait for the import to finish.
4. **Screenshot the post-import summary banner.**
5. Check the wording on the screen for the failed-files breakdown. **You should NOT see** any "files failed for an unclassified reason" sub-bucket. You may still see a sub-bucket for the 18 AutoCAD drawings that need ODA File Converter — that's a separate known issue and is fine.
6. Click **Continue** through to the project dashboard.

**Expected behaviour:** the post-import banner reports added/already-in-project numbers and at most an ODA File Converter remediation row. **No "unclassified failed" row should be visible.**

**If you do see "unclassified failed":** the fix didn't fully land — capture the screenshot and the count, and add it to your feedback document with the heading **"alpha-23 regression: unclassified bucket still present."**

#### Stress check (2 minutes)

The original bug only showed up because the import button was being submitted twice (a separate bug, item #4 in the punch list, still open). Even with that still happening, alpha-23 should handle it cleanly.

1. Reset back to a clean PC (Step 1) and reinstall (Step 2). *(Skip this if you've already started fresh and your test machine is empty.)*
2. Walk through the wizard up to the Import screen as before.
3. **Click the Import button twice in quick succession** — within about a second of each other.
4. Wait for the import to finish.
5. Screenshot the banner.

**Expected behaviour:** same as above — no "unclassified failed" row. The numbers in the banner may look unusual (you might see "already in the project" counts that look high) but that's fine for now — it's the still-open item #4. The important thing is that no successful imports get reported as failures.

### Items still open (you may notice these — please don't be surprised)

These are known and queued; flag if they get worse, otherwise no need to file again:

- **Item #2** — backend log lines all show `level: info` even when something legitimately failed. Hard to spot real errors when grepping.
- **Item #3** — the dashboard reports "0 extracted, X pending" even though extraction has happened. Wording or wiring bug.
- **Item #4** — the import is being submitted twice under the hood. With alpha-23's fix this no longer surfaces as failures, but the log volume is still doubled.

### Other observations from the 02/05 round (not addressed in alpha-23)

These are fixes we discussed but haven't shipped yet:

- The "Status will be on your project dashboard" copy on the post-import screen is misleading; the **Project** button at the top restarts setup instead of showing status.
- The sources page on the project dashboard isn't easy to navigate when you're trying to understand what's pending.
- MS PowerPoint and MS Project files aren't yet supported and get listed as "skipped — unsupported file type."

If you re-encounter any of these on alpha-23, a one-liner ("still present, no change") is enough — no need to re-document.

## Step 4 — What to send back

Send all of the following to the developer (Slack DM or email both work). **Be generous — over-share rather than under-share** so the developer doesn't have to ask follow-ups.

- **Your feedback document** — Word or OneNote, whichever is faster for you. Include screenshots inline rather than as separate attachments where you can.
- **Screenshots** of:
  - the post-import banner (both the targeted-check run and the stress-check run)
  - any error message, weird wording, or unexpected screen
  - any place where you're unsure what to do next
- **The backend log file** from the loaned PC. The canonical path is `C:\Meridian\logs\backend.log` (it may have a date suffix like `meridian-YYYYMMDD.log`). Ship the whole file even if it looks long — the developer can grep it. If you've reset the PC since starting the test, the log only covers from your latest install onwards, which is fine.
- **The time and date** of each test run (so the developer can correlate against the log timestamps).
- **A free-form "anything else weird" section** at the end of your feedback document — anything that surprised you, felt slow, looked broken, or just made you go "huh." Even small wording bugs help.

## When something blocks you completely

If the install fails, the wizard hangs, or any step in this guide doesn't work as written, **stop and write a note describing exactly what happened** (the last button you clicked, the error message word-for-word, a screenshot of the screen). Send that as soon as you've captured it — don't keep grinding through the rest of the guide on a broken install. The faster a blocking issue is reported, the faster a hotfix can ship.
