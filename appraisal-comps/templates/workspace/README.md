# Appraisal Comps workflow

Claude prepares comp data and drafts. You make every selection, adjustment and value conclusion. Claude's rules, the workflow stages and the skills come from the `appraisal-comps` plugin; this folder holds only your settings and data.

## What's in this folder

| Folder | What it holds |
| --- | --- |
| `settings/` | Your conventions (`conventions.md`: measurement rules, ranking weights) and your export name. Updates never overwrite it. |
| `assignments/` | One folder per job. |
| `shared/` | What carries over between jobs: building line measurements, corrections, lessons. |
| `exports/` | Learning exports. You decide whether to send them. |

## What to say to Claude

You don't need any commands. Say what you want:

| To | Say |
| --- | --- |
| start a job | "New assignment: 123 Main St, Brooklyn" |
| read a work site | "Open OneKey MLS" or paste an order link |
| find a county's property data | "Find the tax map for Yonkers" |
| finish a job | "This assignment is done" |
| send what Claude learned | "Run the learning export" |

## Where Claude stops for you

Claude stops and waits for you at each of these:

1. **Intake:** you confirm the subject record.
2. **Pool and rank:** you approve the shortlist.
3. **Comp grid:** you choose the comps and make the adjustments.
4. **Close-out:** you say the assignment is done.
5. **Learning export:** you review what's in it and approve it.

## Work sites in your Chrome

Public sites, such as Zillow, StreetEasy and county property sites, are read by an outside service (browser-use). It is sent the property's address and record numbers and nothing about your client. It sees the public page, and it clears human checks there itself.

Claude reads the work sites in your own Chrome, where you're already logged in. It can also search the web and open any other site, such as a county's tax map or property cards, to find property data. It only reads; it never submits anything. Claude starts with the sites on your list for each job, and asks before adding a new one.

- **Allow the connection.** Setup ticks "Allow remote debugging" in your Chrome. The first time Claude opens a site in a session, Chrome asks you to allow the connection. Click Allow. If Chrome ever asks when you didn't just ask Claude to open a site, click Cancel and tell the maintainer.
- **Logging in.** If a site shows a login page, Claude asks you to log in yourself in Chrome. It never types a password.
- **Its own tab.** Claude opens its pages in a tab of its own and tells you when it does.
- **Human checks.** If a site shows a check like "Press & Hold", "verify you are human" or "I'm not a robot", Claude stops and asks you to clear it in your Chrome yourself, then carries on. It never clicks on these checks.

## Updates

1. **You work as usual.** Claude logs how long each stage takes, and close-out records line measurements, comp choices and corrections.
2. **Weekly, say "Run the learning export".** Review what's in it, approve it, and send the zip from `exports/` to the maintainer yourself.
3. **The maintainer improves the plugin** and publishes a new version.
4. **Install it.** At the start of a chat, Claude says when an update is ready. Say yes, and it gives you the install steps one at a time; start a new chat when it's done. The first time you open a chat after an update, Claude tells you what changed. If an update causes a problem, tell the maintainer, who publishes a fix.

## If something goes wrong

- **"I couldn't check for updates today."** Harmless. Your work carries on, and Claude checks again another day.
- **Claude says an update didn't finish and it can't start work.** Send the line it shows to the maintainer. Claude starts no assignment work until it's fixed.
- **Anything else looks wrong.** Tell Claude what you saw, and tell the maintainer.

## First-time setup

Setup is done once, following the install guide: https://github.com/JonathanShitrit/appraisal-comps-releases. Before your first real job, fill in `settings/conventions.md` with the maintainer; until then, Claude asks you about anything still blank.

## Don't

- Don't sync, zip or share `assignments/` outside the learning export.
- Don't add email, chat, CRM or upload connectors to Claude.
- Don't edit the plugin's files. Send changes back to the maintainer instead.
- Check the work computer's IT rules before installing anything new or using a shared drive.
