import { createHash } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { canonicalJson, sha256, verifyReceipt } from "../common/receipt.mjs";

export const MIN_GRACE_MS = 60_000;
const STATES = ["grace", "claimed", "accepted", "provider_confirmed", "failed", "expired", "unknown", "aborted"];
const CLAIM_MARGIN_MS = 5_000;
function exact(value, fields, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new Error(`invalid_${label}_fields`);
}
function bounded(value, label, max = 512) {
  if (typeof value !== "string" || value.length < 1 || value.length > max) throw new Error(`invalid_${label}`);
  return value;
}
export function parseExecuteRequest(value) {
  exact(value, ["receipt", "execution"], "request");
  exact(value.execution, ["account", "thread_id", "draft_id", "attestation_id"], "execution");
  return {
    receipt: value.receipt,
    execution: {
      account: bounded(value.execution.account, "account", 320),
      threadId: bounded(value.execution.thread_id, "thread_id", 256),
      draftId: bounded(value.execution.draft_id, "draft_id", 256),
      attestationId: bounded(value.execution.attestation_id, "attestation_id", 71),
    },
  };
}
export class DefinitivePrePostRejection extends Error {
  constructor(code) { super(code); this.code = code; }
}
export class ExecutorJournal {
  #db;
  constructor(path) {
    this.#db = new DatabaseSync(path);
    this.#db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=FULL;
      PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS executions(
        receipt_id TEXT PRIMARY KEY,
        receipt_hash TEXT NOT NULL UNIQUE,
        execution_hash TEXT NOT NULL,
        binding_hash TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN (${STATES.map((state) => `'${state}'`).join(",")})),
        not_before_ms INTEGER NOT NULL,
        expires_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        provider_message_id_sha256 TEXT,
        error_code TEXT
      );
      CREATE TABLE IF NOT EXISTS audit_transitions(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_id TEXT NOT NULL,
        receipt_hash TEXT NOT NULL,
        binding_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        at TEXT NOT NULL,
        error_code TEXT
      );
      CREATE TRIGGER IF NOT EXISTS audit_insert AFTER INSERT ON executions BEGIN
        INSERT INTO audit_transitions(receipt_id,receipt_hash,binding_hash,state,at,error_code)
        VALUES(NEW.receipt_id,NEW.receipt_hash,NEW.binding_hash,NEW.state,NEW.updated_at,NEW.error_code);
      END;
      CREATE TRIGGER IF NOT EXISTS audit_update AFTER UPDATE OF state ON executions BEGIN
        INSERT INTO audit_transitions(receipt_id,receipt_hash,binding_hash,state,at,error_code)
        VALUES(NEW.receipt_id,NEW.receipt_hash,NEW.binding_hash,NEW.state,NEW.updated_at,NEW.error_code);
      END;
    `);
  }
  recoverInterruptedClaims(now = new Date().toISOString()) {
    this.#db.prepare("UPDATE executions SET state='unknown',updated_at=?,error_code='interrupted_after_claim' WHERE state='claimed'").run(now);
    this.#db.prepare("UPDATE executions SET state='expired',updated_at=?,error_code='expired_during_restart' WHERE state='grace' AND expires_at<=?")
      .run(now, now);
  }
  start({ receiptId, receiptHash, executionHash, bindingHash, revisionId, expiresAt, nowMs }) {
    const at = new Date(nowMs).toISOString();
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const prior = this.entry(receiptId);
      if (prior) {
        if (prior.receiptHash !== receiptHash || prior.executionHash !== executionHash || prior.bindingHash !== bindingHash)
          throw new Error("receipt_replay_conflict");
        this.#db.exec("COMMIT");
        return prior;
      }
      this.#db.prepare(`INSERT INTO executions(
        receipt_id,receipt_hash,execution_hash,binding_hash,revision_id,state,not_before_ms,expires_at,updated_at
      ) VALUES(?,?,?,?,?,'grace',?,?,?)`).run(
        receiptId, receiptHash, executionHash, bindingHash, revisionId, nowMs + MIN_GRACE_MS, expiresAt, at,
      );
      this.#db.exec("COMMIT");
      return this.entry(receiptId);
    } catch (error) { this.#db.exec("ROLLBACK"); throw error; }
  }
  claim(receiptId, nowMs) {
    const at = new Date(nowMs).toISOString();
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const current = this.entry(receiptId);
      if (!current) throw new Error("execution_not_found");
      if (current.state !== "grace") { this.#db.exec("COMMIT"); return { claimed: false, status: current }; }
      if (nowMs < current.notBeforeMs) throw new Error("grace_incomplete");
      if (Date.parse(current.expiresAt) <= nowMs) throw new Error("receipt_expired_before_claim");
      const update = this.#db.prepare("UPDATE executions SET state='claimed',updated_at=? WHERE receipt_id=? AND state='grace'").run(at, receiptId);
      if (update.changes !== 1) throw new Error("claim_race");
      this.#db.exec("COMMIT");
      return { claimed: true, status: this.entry(receiptId) };
    } catch (error) { try { this.#db.exec("ROLLBACK"); } catch {} throw error; }
  }
  abort(receiptId, nowMs) {
    const at = new Date(nowMs).toISOString();
    const update = this.#db.prepare("UPDATE executions SET state='aborted',updated_at=?,error_code='human_abort' WHERE receipt_id=? AND state='grace'")
      .run(at, receiptId);
    const status = this.entry(receiptId);
    if (!status) throw new Error("execution_not_found");
    if (update.changes !== 1 && status.state !== "aborted") throw new Error("execution_not_abortable");
    return status;
  }
  failGrace(receiptId, state, nowMs, errorCode) {
    if (!["failed", "expired"].includes(state)) throw new Error("invalid_grace_failure_state");
    const at = new Date(nowMs).toISOString();
    const update = this.#db.prepare("UPDATE executions SET state=?,updated_at=?,error_code=? WHERE receipt_id=? AND state='grace'")
      .run(state, at, errorCode, receiptId);
    const status = this.entry(receiptId);
    if (!status) throw new Error("execution_not_found");
    if (update.changes !== 1 && status.state !== state) throw new Error("invalid_execution_transition");
    return status;
  }
  finish(receiptId, state, nowMs, { providerMessageId, errorCode } = {}) {
    if (!["accepted", "provider_confirmed", "failed", "unknown"].includes(state)) throw new Error("invalid_finish_state");
    const at = new Date(nowMs).toISOString();
    const providerHash = providerMessageId ? sha256(providerMessageId) : null;
    const update = this.#db.prepare(`UPDATE executions SET state=?,updated_at=?,provider_message_id_sha256=?,error_code=?
      WHERE receipt_id=? AND state='claimed'`).run(state, at, providerHash, errorCode || null, receiptId);
    const status = this.entry(receiptId);
    if (!status) throw new Error("execution_not_found");
    if (update.changes !== 1 && status.state !== state) throw new Error("invalid_execution_transition");
    return status;
  }
  entry(receiptId) {
    const row = this.#db.prepare("SELECT * FROM executions WHERE receipt_id=?").get(receiptId);
    if (!row) return undefined;
    return {
      receiptId: row.receipt_id, receiptHash: row.receipt_hash, executionHash: row.execution_hash,
      bindingHash: row.binding_hash, revisionId: row.revision_id, state: row.state,
      notBeforeMs: row.not_before_ms, expiresAt: row.expires_at, updatedAt: row.updated_at,
      providerMessageIdSha256: row.provider_message_id_sha256 || undefined,
      errorCode: row.error_code || undefined,
    };
  }
  auditColumns() { return this.#db.prepare("PRAGMA table_info(audit_transitions)").all().map((row) => row.name); }
}

export class SendExecutor {
  #inflight = new Map();
  constructor({ journal, provider, trust, now = () => Date.now(), sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) {
    this.journal = journal; this.provider = provider; this.trust = trust; this.now = now; this.sleep = sleep;
  }
  async execute(value) {
    const request = parseExecuteRequest(value);
    const receiptId = request.receipt?.receipt_id;
    if (typeof receiptId !== "string") throw new Error("invalid_receipt_id");
    const active = this.#inflight.get(receiptId);
    if (active) return await active;
    const execution = this.#executeOnce(request).finally(() => this.#inflight.delete(receiptId));
    this.#inflight.set(receiptId, execution);
    return await execution;
  }
  async #executeOnce({ receipt, execution }) {
    if (sha256(execution.account.toLowerCase()) !== receipt.binding.account_email_sha256 ||
        sha256(execution.threadId) !== receipt.binding.thread_id_sha256 ||
        sha256(execution.draftId) !== receipt.binding.draft_id_sha256 ||
        execution.attestationId !== receipt.binding.attestation_id)
      throw new Error("execution_binding_mismatch");
    const receiptHash = sha256(receipt);
    const executionHash = sha256({
      account: execution.account.toLowerCase(), thread_id: execution.threadId,
      draft_id: execution.draftId, attestation_id: execution.attestationId,
    });
    const bindingHash = sha256(receipt.binding);
    const prior = this.journal.entry(receipt.receipt_id);
    if (prior) {
      if (prior.receiptHash !== receiptHash || prior.executionHash !== executionHash || prior.bindingHash !== bindingHash)
        throw new Error("receipt_replay_conflict");
      return prior;
    }
    const verified = verifyReceipt(receipt, { trust: this.trust, now: this.now() });
    if (Date.parse(verified.expiresAt) < this.now() + MIN_GRACE_MS + CLAIM_MARGIN_MS)
      throw new Error("receipt_lifetime_insufficient_for_grace");
    const rendered = await this.provider.render(execution);
    exact(rendered, ["revision_id", "draft_fingerprint", "approval_binding"], "rendered_draft");
    if (canonicalJson(rendered.approval_binding) !== canonicalJson(receipt.binding) ||
        rendered.draft_fingerprint !== receipt.binding.outgoing_fingerprint)
      throw new Error("rerender_binding_mismatch");
    let status = this.journal.start({
      receiptId: verified.receiptId, receiptHash, executionHash, bindingHash,
      revisionId: rendered.revision_id, expiresAt: verified.expiresAt, nowMs: this.now(),
    });
    if (status.state !== "grace") return status;
    while (this.now() < status.notBeforeMs) {
      await this.sleep(Math.min(250, status.notBeforeMs - this.now()));
      status = this.journal.entry(verified.receiptId);
      if (!status || status.state !== "grace") return status;
    }
    let fresh;
    try {
      fresh = await this.provider.render(execution);
      if (fresh.revision_id !== status.revisionId || fresh.draft_fingerprint !== receipt.binding.outgoing_fingerprint ||
          canonicalJson(fresh.approval_binding) !== canonicalJson(receipt.binding))
        throw new DefinitivePrePostRejection("draft_changed_during_grace");
    } catch (error) {
      const code = error instanceof DefinitivePrePostRejection ? error.code : "rerender_failed_before_claim";
      return this.journal.failGrace(verified.receiptId, "failed", this.now(), code);
    }
    let claim;
    try {
      claim = this.journal.claim(verified.receiptId, this.now());
    } catch (error) {
      if (error instanceof Error && error.message === "receipt_expired_before_claim")
        return this.journal.failGrace(verified.receiptId, "expired", this.now(), "receipt_expired_before_claim");
      throw error;
    }
    if (!claim.claimed) return claim.status;
    try {
      const result = await this.provider.send(execution, {
        revisionId: fresh.revision_id,
        draftFingerprint: fresh.draft_fingerprint,
        delaySeconds: receipt.binding.delay_seconds,
      });
      if (result.provider_confirmed)
        return this.journal.finish(verified.receiptId, "provider_confirmed", this.now(), { providerMessageId: result.provider_message_id });
      if (result.accepted)
        return this.journal.finish(verified.receiptId, "accepted", this.now(), { errorCode: "provider_confirmation_pending" });
      return this.journal.finish(verified.receiptId, "unknown", this.now(), { errorCode: "provider_outcome_unknown" });
    } catch (error) {
      if (error instanceof DefinitivePrePostRejection)
        return this.journal.finish(verified.receiptId, "failed", this.now(), { errorCode: error.code });
      return this.journal.finish(verified.receiptId, "unknown", this.now(), { errorCode: "provider_outcome_unknown" });
    }
  }
  status(receiptId) { return this.journal.entry(receiptId); }
  abort(receiptId) { return this.journal.abort(receiptId, this.now()); }
}
