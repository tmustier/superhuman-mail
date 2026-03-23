# Changelog

## v0.2.0 — 2026-03-23

This release includes **all changes merged since `v0.1.1`**.

### Highlights

- restructured the `shm` CLI around a clearer, more agent-friendly surface
- added first-class read-receipt / Recent Opens support via `opens`
- aligned the Python client and repo docs with the new command model
- shipped and restored a full set of reverse-engineered Superhuman endpoint docs
- included timezone / recipient fixes and improved skill discoverability from earlier unreleased commits

### Breaking CLI changes

- `shm thread read <thread_id>` → `shm thread messages <thread_id>`
- `shm thread read-statuses <thread_id>` → `shm opens <thread_id>`
- `shm share <thread_id> <draft_id>` → `shm draft share <thread_id> <draft_id>`
- `shm unshare <thread_id> <draft_id>` → `shm draft unshare <thread_id> <draft_id>`

### Added

- top-level `opens` command for per-thread read receipts
- `shm opens --recent` backed by the local `activity_feed` cache
- `--recipient` filtering for both opens modes
- `superhuman_mail/opens.py`
- `python -m superhuman_mail` support via `superhuman_mail/__main__.py`
- deep endpoint docs:
  - AI
  - agent sessions
  - calendar
  - CRM
  - knowledge/links/containers
  - labels
  - messages/send
  - read statuses / Recent Opens
  - teams/misc

### Changed

- `thread userdata` is now explicitly documented as advanced/raw
- Python client now exposes:
  - `client.thread.messages(...)`
  - `client.opens.per_thread(...)`
  - `client.opens.recent(...)`
  - `client.draft.share(...)`
  - `client.draft.unshare(...)`
- backward-compatible Python client aliases were kept for older callers where practical
- README and SKILL documentation were rewritten to match the shipped command surface

### Fixed

- `emit()` now supports explicit exit-code overrides
- `thread list --fail-empty` now exits correctly
- draft envelopes now report the correct command name:
  - `draft.reply`
  - `draft.reply-all`
  - `draft.forward`
  - `draft.compose`
- `opens --recent` now uses the actual opened `message_id` for metadata
- forward recipient handling was fixed
- timezone detection now strips `posix/` and `right/` prefixes and avoids bad abbreviation fallbacks

### Notes

- This is a **minor** bump because the CLI surface changed materially and gained new user-facing functionality.
- If you have scripts using the old CLI names, update them to the new command forms above.
