# Meridian Setup

## What is this?

Meridian reads through the documents on your construction project (PDFs,
Word files, Excel workbooks, drawings, emails) and builds a single
spreadsheet that lists what each trade needs to deliver, when, and to whom.
This folder contains the one-click installer that puts Meridian on your
Windows computer. You do not need to know anything about Python or
PowerShell.

## How to install Meridian

1. **Download the installer.** Go to
   <https://github.com/profixel660/meridian-trace/releases/latest> and
   download both files into the same folder (your Desktop is fine):
   - `Install-Meridian.bat`
   - `Install-Meridian.ps1`

2. **Right-click `Install-Meridian.bat`** in File Explorer, then choose
   **Run as administrator**. Windows will pop up a blue or yellow box
   asking "Do you want to allow this app to make changes to your device?"
   Click **Yes**. The installer needs administrator access to install
   Python and to turn on a Windows setting that Meridian relies on.

3. **Follow the prompts.** The installer prints a running commentary of
   what it is doing. It will:
   - Install Python (about 25 MB download).
   - Download Meridian and the libraries it needs (about 200 MB).
   - Ask you to paste your **Anthropic API key**. (You get this from
     <https://console.anthropic.com> -- it is the password Meridian uses to
     read your documents with Claude. Keep it private; do not share it.)
   - Put a **Meridian** icon on your Desktop.
   - Walk you through your first project.

   Total time: 5 to 15 minutes. Most of that is waiting for files to
   download. If the screen looks frozen for a minute, that is normal --
   downloads do not always show progress.

## First time you launch Meridian

The installer ends by running `meridian init`, which is a guided wizard.
It walks you through:

- **Two-factor sign-in (optional).** You can skip this and turn it on
  later. If you do enrol, scan the QR code with an authenticator app on
  your phone (Microsoft Authenticator, Google Authenticator, Authy --
  any of them).
- **Your first project.** Just give it a name (e.g. "Glasshouse Tower").
- **Your first document.** Type or paste the path to one of your project
  files (a PDF, Word doc, Excel workbook, .msg email, etc.) and Meridian
  imports it.
- **Bootstrap pass.** Meridian asks Claude to scan a sample of your
  document and figure out which trades are involved and what they need
  to deliver. This is the "wow" moment.

## Where things live

Everything Meridian creates lives under `C:\Meridian\`:

| Folder or file               | What it is                               |
|------------------------------|------------------------------------------|
| `C:\Meridian\projects\`      | One SQLite file per project. Your data.  |
| `C:\Meridian\.env`           | Your Anthropic API key.                  |
| `C:\Meridian\venv\`          | The Python environment Meridian runs in. |
| `C:\Meridian\install.log`    | What the installer did, line by line.    |

**To back up your work:** copy the entire `C:\Meridian\` folder to a USB
drive, network share, or OneDrive. To restore on a new machine: install
Meridian fresh on the new machine, then copy your `projects\` folder
back into `C:\Meridian\projects\`.

## If something goes wrong

The installer logs every step to `C:\Meridian\install.log`. If you hit
an error, send that file to support along with a description of what
happened, and we can usually figure it out from the log alone.

Common things that go wrong:

- **"Cannot reach api.github.com."** Your internet is down, or you are
  on a corporate network that blocks GitHub. Try again from home Wi-Fi,
  or ask your IT team to allow `github.com` and `python.org`.
- **"pip install failed."** Almost always a network blip during the
  download. Run the installer again -- it will pick up where it left off.
- **The installer window closes immediately.** You probably double-clicked
  the `.ps1` file instead of the `.bat` file. Use the `.bat` file.

## How to uninstall

Two options:

1. If you still have `C:\Meridian\` on your machine, the simplest path is
   to download `Uninstall-Meridian.bat` and `Uninstall-Meridian.ps1`
   from the same release page, save them anywhere, right-click the
   `.bat`, and choose **Run as administrator**.

2. The uninstaller asks you separately about every piece of data: your
   projects, your API key, the two-factor secret, the Windows long-path
   setting, and Python itself. The default for each is **keep**, so if
   you just press Enter through everything, you only lose Meridian
   itself -- not your work and not anything that other programs might
   need.

## A note on the SmartScreen warning

The first time you run `Install-Meridian.bat`, Windows may show a blue
box that says **"Microsoft Defender SmartScreen prevented an unrecognised
app from starting."** This is because Meridian is not yet **code-signed**
(a step that costs money and time, planned for a future release). To get
past it: click **More info** in that box, then click the **Run anyway**
button that appears.

This is a real friction point. We know it is unsettling. It does not
mean anything is wrong with your computer or with Meridian -- only that
Windows has not yet seen enough copies of Meridian "in the wild" to
trust it automatically. The same warning appears for most small-publisher
software the first time you run it.
