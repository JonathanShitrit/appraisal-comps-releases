# Appraisal Comps workflow

Claude prepares comp data and drafts. The appraiser makes every selection, adjustment and value conclusion. The agent's rules, the workflow stages and the skills all come from the `appraisal-comps` plugin; this folder holds only your settings and data.

## Where each thing lives (one place per topic)

| Topic | File |
| --- | --- |
| Agent rules, workflow stages, folder ownership | shown by the plugin at the start of every session |
| Step-by-step for each stage | the plugin's skills, run automatically for each stage |
| Measurement conventions, ranking weights | `settings/conventions.md` |
| Which sites the browser can read | built into the plugin (OneKey MLS, Zillow, AppraisalPort, Reggora, ValueConnect, DwellingBlocks, Aivre) |
| What changed in each version | shown by the plugin when an update installs |

## What to say to Claude

You don't need any commands. Say what you want:

| To | Say |
| --- | --- |
| set up this folder (once) | "Set up this folder" |
| start a job | "New assignment: 123 Main St, Brooklyn" |
| read a work site | "Open Zillow" or paste a listing link |
| finish a job | "This assignment is done" |
| send what Claude learned | "Run the learning export" |

## The update loop

1. **Appraiser works** as usual. Each stage logs timing, and close-out records line measurements, comp choices and corrections.
2. **Weekly export.** He asks Claude to "run the learning export", reviews the manifest and leak scan, approves, and sends the zip from `exports/` to the maintainer himself.
3. **Maintainer improves** the plugin and publishes a new version.
4. **Install the update.** Claude says at session start when an update is ready. Say yes, and it gives you the install steps one at a time; start a new chat when it's done. There's no undo: if an update causes a problem, tell the maintainer, who publishes a fix.

## First-time setup (once)

- In the Claude desktop app: Customize → Directory → Plugins → Personal → **+** → Add marketplace → `JonathanShitrit/appraisal-comps-releases`, then install `appraisal-comps`.
- Select an empty folder for this workflow (not one synced by OneDrive, iCloud or another sync service).
- Say "Set up this folder". Claude creates this folder's files, runs the test suite, and asks for your contributor code and export mode.
- Confirm `python3` runs and HTTPS downloads work, and that Node is installed (the browser needs it).

## Don't

- Don't sync, zip or share `assignments/` or `settings/export.json`.
- A firm-mode export holds comp prices, adjustments and values. Move it only through firm-controlled storage or as an encrypted file, never plain email.
- Don't add email, chat, CRM or upload connectors to this Claude project.
- Don't edit the plugin's files. Send changes back to the maintainer instead.
- Check the work computer's IT rules before installing anything or using a shared drive.
