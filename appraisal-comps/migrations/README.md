# Migrations

For the maintainer. A migration changes the appraiser's own data (`settings/`, `shared/`, `assignments/`, `exports/`) when an update needs it.

- **File name:** `<X>_<Y>_<Z>_<slug>.py`, for example `0_6_0_rename_notes.py`. `X.Y.Z` is the release that brings it. Other files here are ignored.
- **Contents:** one function, `def migrate(root: Path) -> None`, where `root` is the Appraisal Comps folder.
- **Which ones run:** at the first session on a new version, the SessionStart hook (`hooks/session_start.py`, via `tools/migrations.py`) runs every migration with a version above the old one and at or below the new one, in version order (then by name). Then it runs `ledger.py check`. Any failure leaves `last_seen_version` where it was and retries next session.
- **Must skip work already done.** A migration can run again after a failed install, so check before changing anything.
- **Must leave data readable by the version before it.** There is no undo (a bad release is fixed by the next one), and `ledger.py check` requires exact headers, so don't add or reorder CSV columns.
