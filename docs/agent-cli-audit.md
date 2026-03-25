# shm CLI — Agent-Friendliness Audit

_25 March 2026_

Audit of `shm` against agent-friendly CLI design principles. What we already do well, what's worth fixing, and what to leave alone.

## Context

`shm` was built agent-first — it's an unofficial CLI for Superhuman's API, designed to be driven by AI agents (primarily Pi). It already follows many agent-CLI best practices by design. This audit checks it against a broader set of principles to find remaining gaps.

## What we already nail

**Non-interactive.** Zero interactive prompts. Every input is a flag or positional arg. An agent never gets stuck on a "which environment?" prompt.

**Progressive discovery.** `shm` shows subcommands → `shm thread --help` shows actions → `shm thread messages --help` shows flags. Plus `shm schema` for machine-readable introspection. An agent can drill down without drowning in irrelevant docs.

**Predictable command structure.** Consistent `resource action` pattern: `thread list`, `thread search`, `draft reply`, `draft read`, `draft discard`, `comment post`, `comment read`, `comment discard`. An agent that learns one resource can guess the others.

**Structured JSON output.** Every command returns the same envelope:
```json
{"status": "succeeded|failed", "command": "...", "data": {...}, "errors": [], "warnings": []}
```
Errors are classified with `class`, `code`, `retryable`, and `hint`. Agents can branch on error class without parsing human prose.

**Dry-run for destructive actions.** `send` requires either `--dry-run` or `--confirm` — you can't accidentally send. The safe path is the only path.

**No interactive confirmations.** No "are you sure?" prompts to bypass. Send uses an explicit `--confirm` flag. Nothing to `--yes` past.

**Actionable error hints.** When something fails, errors include what to do: `"hint": "Restart Superhuman app or run shm doctor"`, `"hint": "Rate limited — wait and retry"`. Agents self-correct from these.

**Fail-fast on missing context.** Running `shm draft` with no action immediately returns a JSON error with the valid action list, not a hang or a wall of help text.

## Three things worth fixing

### 1. `--help` has no examples

**The problem.** `shm draft reply --help` shows bare argparse output:
```
positional arguments:
  thread_id

options:
  --body BODY
  --scheduled-for SCHEDULED_FOR
```

Agents pattern-match off examples faster than they parse argument descriptions. The examples already exist — they're in the `SCHEMA` dict, accessible via `shm schema draft.reply` — but agents don't naturally run `shm schema`. They run `--help`.

**The fix.** Add an `epilog` to every argparse subparser, pulling examples from `SCHEMA`. Expand `SCHEMA` from a single `example` string to an `examples` list so we can show the common patterns:

```
Examples:
  shm draft reply 19d001f35612a211 --body 'Thanks for the update'
  shm draft reply 19d001f35612a211 --body 'See you then' --scheduled-for '2026-03-26T09:00:00Z'
  shm draft reply 19d001f35612a211 --body 'Confirming' --reminder '2026-03-28T10:00:00Z'
```

**Effort:** Small. Mechanical wiring of existing data into argparse epilogs.

### 2. `--body` can't read from stdin or a file

**The problem.** Composing a long email means a giant quoted string on the command line:
```bash
shm draft reply abc123 --body "Three paragraphs of carefully formatted text with quotes and newlines..."
```

Agents think in pipelines. They want to write to a temp file, then pass it in. Or pipe from another command. Neither works today.

**The fix.** Two additions:
- `--body-file <path>` — read body from a file
- `--body -` — the unix convention for "read from stdin"

```bash
# File
shm draft reply abc123 --body-file ./email.txt

# Stdin pipe
echo "Reply body" | shm draft reply abc123 --body -

# Same for --body-html
shm draft reply abc123 --body-file body.txt --body-html-file body.html
```

**Effort:** Small. Read file/stdin before passing to the existing draft creation functions.

### 3. Missing-arg errors bypass the JSON envelope

**The problem.** When a required arg is missing, argparse prints plain text to stderr and exits with code 2:
```
shm draft reply: error: the following arguments are required: thread_id, --body
```

This breaks the contract. Every other error path returns the JSON envelope. An agent parsing stdout for JSON gets nothing; the error is on stderr in a format it doesn't expect.

**The fix.** Override argparse's `error()` method to emit our standard JSON envelope instead:
```json
{
  "status": "failed",
  "command": "draft.reply",
  "errors": [{
    "class": "input",
    "code": "MISSING_ARGS",
    "retryable": false,
    "hint": "Required: thread_id, --body\n  Example: shm draft reply 19d001f35612a211 --body 'Thanks for the update'"
  }]
}
```

Now the agent gets structured JSON with the example of the correct invocation — exactly what the article recommends.

**Effort:** Small-medium. Subclass `ArgumentParser` to override `error()` and `exit()`. Need to map argparse's error messages to our envelope format.

## What's not worth changing

**JSON-only output.** No `--format text|json` flag. This is an agent CLI. Adding a human-readable text mode would be scope creep with no user.

**Idempotency on writes.** Running `draft reply` twice creates two drafts. This is an API limitation — Superhuman doesn't support idempotency keys. We could add client-side dedup (hash body + thread_id, check for recent duplicates) but it'd be fragile and the failure mode (suppressing a legitimate draft) is worse than the duplicate mode. Agents already handle this fine through the draft → dry-run → send workflow.

**`--version` flag.** Nice to have, not blocking anything.

## Priority order

1. **`--help` examples** — highest impact, lowest effort. An agent's first instinct is `--help`, and right now it gets bare args with no patterns to match against.
2. **JSON envelope on arg errors** — breaks the API contract. Every error should be structured. This also applies to bare `shm` / unknown commands, which currently call `parser.print_help()` — same bypass, same fix.
3. **`--body` from stdin/file** — quality of life. Makes long emails and pipelines work cleanly. Note: `--body` is currently `required=True`, so adding `--body-file` means making `--body` optional and validating exactly-one-of in application code (not argparse) — which should emit a JSON envelope error, tying back to #2.
4. **`--version`** — 2-line change, useful for debugging when `shm` is installed in multiple places.

## Review notes (GPT 5.4)

- **Audit is accurate.** All three findings verified against the code. SCHEMA data exists but isn't wired to argparse epilogs, no stdin/file body support, and `parser.parse_args()` bypasses the JSON envelope on missing args (exit code 2, plain text to stderr).

- **Priority order is correct.** `--help` examples is the right #1.

- **Missed a fourth envelope bypass: `parser.print_help()`.** Bare `shm` and unknown commands call `print_help()` and return exit code 1 — no JSON on stdout. Same class of bug as #3. _(Folded into priority #2 above.)_

- **`--body-file` is underspecified.** `--body` is currently `required=True` on all draft commands. Adding `--body-file` means making `--body` optional and validating exactly-one-of in application code. That validation should also emit a JSON envelope error — loops back to #3. _(Folded into priority #3 above.)_

- **`--version` dismissed too quickly.** Not about human niceties — when an agent gets unexpected behavior, `shm --version` is the fastest way to confirm which version is running (multiple venvs, different paths). 2-line change. _(Added as priority #4.)_

- **"What we already nail" section is honest.** The envelope design (`classify_exception` in `_envelope.py`) mapping HTTP status codes into agent-parseable classes with retry hints is particularly solid.

- **JSON-only output decision is correct** for an unstated reason: the SKILL.md teaches the agent to expect JSON. A text mode would create a second contract to maintain for zero consumers.

- **Idempotency dismissal is sound.** Client-side dedup has a worse failure mode (silent suppression) than the duplicate mode (extra draft that's visible and deletable).
