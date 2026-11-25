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
4. Ask him, once, to open `chrome://inspect/#remote-debugging` in his Chrome and tick "Allow remote debugging". Then tell him he's ready: to start a job, he says "new assignment" with the subject's address. To read a work site, he names it, like "Open OneKey MLS"; Chrome asks him to allow the connection the first time in a session, and he logs in in his own Chrome if he isn't already (`browser-read` skill). If the browser tools are missing from the chat, `uv` isn't installed on his Mac: point him to the maintainer.
5. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/cloud_read.py check-key` from his folder. If it says the key is OK, tell him how much credit is left. If it says there is no key, tell him the maintainer adds it on the setup call; public sites are read in his Chrome until then. Never ask him to paste a key into the chat.

## Done when

The tests pass, `settings/export.json` exists, and he knows how to start a job.
