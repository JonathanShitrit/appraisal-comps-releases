---
name: setup
description: Set up the Appraisal Comps folder the first time the plugin runs in it. Use when he says "set up", "get started" or "install", or the session start says there's no Appraisal Comps folder yet.
---

# Setup

Goal: turn the folder the appraiser selected into a working Appraisal Comps folder. Keep it to two short messages: his name, then "ready".

## Steps

1. Find the folder he selected for this chat, as an absolute path your shell sees (on the chat side, that is under `~/mnt/`). Run:
   `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/setup_workspace.py --into <that folder>`
   Don't show the file list. If any file was `kept`, name those files in one line (his existing files were left alone).
2. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tests/run_tests.py`. It must pass; say "The plugin's self-check passed." without the counts. If anything fails, stop and show him the failing lines to send to the maintainer; don't continue setup on a broken plugin.
3. Ask him for the name his learning exports go under: lowercase letters and digits, like `dcohen` (hyphens between, like `d-cohen`, are fine). Then run:
   `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/setup_workspace.py --into <that folder> --name <name>`
   It keeps every file from step 1 and saves his name. If it refuses the name, show him the example it gives and ask again.
4. Ask him, once, to install the Playwright Extension in his Chrome, in the profile where he's logged in to the work sites: https://chromewebstore.google.com/detail/mmlmfjhmonkocbjadbfplnigmagldckm. Then tell him he's ready: to start a job, he says "new assignment" with the subject's address. To read a work site, he names it, like "Open Zillow"; he approves the connect page once per session, and logs in in his own Chrome if he isn't already (`browser-read` skill).

## Done when

The tests pass, `settings/export.json` exists, and he knows how to start a job.
