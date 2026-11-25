# Appraisal Comps

The `appraisal-comps` plugin for the Claude desktop app. Claude prepares comp data and drafts. You make every selection, adjustment and value conclusion.

## Install (once)

1. **Check the tools.** Open Terminal and run `python3 --version` and `uv --version`; each should print a version number. If `uv` doesn't, install it with `curl -LsSf https://astral.sh/uv/install.sh | sh`. Google Chrome must be installed too.
2. **Allow remote debugging in Chrome.** In the Chrome profile where you log in to the work sites, open `chrome://inspect/#remote-debugging` and tick "Allow remote debugging". Then run `uvx --from 'browser-harness[mcp]==0.1.14' browser-harness telemetry disable` once. If you installed the Playwright Extension before, you can remove it.
3. **Install the plugin.** In the Claude desktop app: Customize → Plugins → Discover → Personal → **+** → Add from a repository → `JonathanShitrit/appraisal-comps-releases`, then install `appraisal-comps`.
4. **Pick an empty folder** for this work. It must not be synced by OneDrive, iCloud or another sync service. Select it for the chat.
5. **Say "Set up this folder".** Claude runs a self-check, then asks for the name your learning exports go under (lowercase, like `dcohen`). If the self-check fails, send the lines it shows to the maintainer.
6. **Fill in your conventions with the maintainer** before your first real job: `settings/conventions.md` in your folder (measurement rules, ranking weights). Until then, Claude asks you about anything still blank.

After setup, the `README.md` in your folder covers everyday use.
