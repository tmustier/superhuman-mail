import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { OUTGOING_FIELDS, validateAttestationBundle } from "../common/attestation.mjs";
import { bindingFromEvidence, canonicalJson, sha256, validateBinding } from "../common/receipt.mjs";

const HASH = /^sha256:[a-f0-9]{64}$/;
function exact(value, fields, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new Error(`invalid_${label}_fields`);
}
function hash(value, label) {
  if (typeof value !== "string" || !HASH.test(value)) throw new Error(`invalid_${label}`);
  return value;
}
function bounded(value, label, max = 256) {
  if (typeof value !== "string" || value.length < 1 || value.length > max) throw new Error(`invalid_${label}`);
  return value;
}
export function verifySlackRequest({ secret, timestamp, signature, rawBody, now = Date.now() }) {
  if (!/^\d{10}$/.test(timestamp) || Math.abs(now - Number(timestamp) * 1000) > 5 * 60_000)
    throw new Error("stale_slack_request");
  if (!/^v0=[a-f0-9]{64}$/.test(signature)) throw new Error("invalid_slack_signature");
  const expected = `v0=${createHmac("sha256", secret).update(`v0:${timestamp}:${rawBody}`).digest("hex")}`;
  const left = Buffer.from(signature);
  const right = Buffer.from(expected);
  if (left.length !== right.length || !timingSafeEqual(left, right)) throw new Error("invalid_slack_signature");
}

function readableHtml(value) {
  return String(value || "")
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<\/(p|div|li|tr|h[1-6])\s*>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'").replace(/&quot;/g, '"');
}
export function buildApprovalPresentation(evidence, binding) {
  const payload = evidence.outgoing_payload;
  exact(payload, OUTGOING_FIELDS, "outgoing_payload");
  const outgoing = Object.fromEntries(OUTGOING_FIELDS.map((field) => [field, payload[field]]));
  return {
    schema: "shm-slack-approval-presentation/v1",
    account: evidence.account,
    thread_id: evidence.thread_id,
    draft_id: evidence.draft_id,
    attestation_id: evidence.attestation_id,
    renderer: evidence.renderer,
    screenshot_sha256: evidence.screenshot_sha256,
    delay_seconds: evidence.delay_seconds,
    outgoing: { ...outgoing, html_body_text: readableHtml(payload.html_body) },
    approval_binding: binding,
  };
}

export function decisionFromSocketPayload({ store, payload, teamId, appId, channel, approvalKeyword }) {
  if (!payload || payload.type !== "event_callback" || payload.team_id !== teamId || payload.api_app_id !== appId ||
      typeof payload.event_id !== "string" || !payload.event_id) throw new Error("slack_context_mismatch");
  const event = payload.event;
  if (!event || event.type !== "message" || typeof event.user !== "string" || !event.user ||
      event.channel !== channel || typeof event.thread_ts !== "string" || typeof event.ts !== "string" ||
      typeof event.text !== "string" || event.subtype !== undefined || event.bot_id !== undefined ||
      event.edited !== undefined || event.hidden === true) throw new Error("invalid_slack_decision_event");
  const context = {
    teamSha256: sha256(teamId), appSha256: sha256(appId),
    channelSha256: sha256(event.channel), threadSha256: sha256(event.thread_ts),
  };
  const pending = store.findPendingBySlackContext(context);
  if (event.text !== `${approvalKeyword} ${pending.nonce}`) throw new Error("approval_keyword_mismatch");
  return {
    requestId: pending.requestId,
    event: {
      principal: `slack:${event.user}`, team_sha256: context.teamSha256, app_sha256: context.appSha256,
      channel_sha256: context.channelSha256, thread_sha256: context.threadSha256,
      approval_event_id: payload.event_id, decision: "approve", nonce: pending.nonce,
    },
  };
}

export class IssuerStore {
  #db;
  constructor(path) {
    this.#db = new DatabaseSync(path);
    this.#db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=FULL;
      PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS approval_requests(
        request_id TEXT PRIMARY KEY,
        binding_json TEXT NOT NULL,
        binding_sha256 TEXT NOT NULL,
        presentation_sha256 TEXT NOT NULL,
        expected_principal TEXT NOT NULL,
        team_sha256 TEXT NOT NULL,
        app_sha256 TEXT NOT NULL,
        channel_sha256 TEXT NOT NULL,
        thread_sha256 TEXT NOT NULL,
        nonce TEXT NOT NULL UNIQUE,
        issued_at TEXT,
        expires_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','approved_waiting_signature','issued','cancelled','expired')),
        approval_event_id TEXT,
        approval_event_sha256 TEXT,
        receipt_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE UNIQUE INDEX IF NOT EXISTS approval_event_once
      ON approval_requests(approval_event_id) WHERE approval_event_id IS NOT NULL;
      CREATE TABLE IF NOT EXISTS audit_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        event TEXT NOT NULL,
        at TEXT NOT NULL,
        binding_sha256 TEXT NOT NULL,
        approval_event_sha256 TEXT
      );
    `);
  }
  create({ requestId, nonce, binding, presentationSha256, expectedPrincipal, teamSha256, appSha256, channelSha256, threadSha256, ttlSeconds, now }) {
    validateBinding(binding);
    hash(presentationSha256, "presentation_sha256");
    hash(teamSha256, "team_sha256");
    hash(appSha256, "app_sha256");
    hash(channelSha256, "channel_sha256");
    hash(threadSha256, "thread_sha256");
    bounded(expectedPrincipal, "expected_principal", 128);
    if (!Number.isSafeInteger(ttlSeconds) || ttlSeconds < 1 || ttlSeconds > 300) throw new Error("invalid_ttl");
    if (!/^sha256:[a-f0-9]{64}$/.test(requestId)) throw new Error("invalid_request_id");
    if (typeof nonce !== "string" || nonce.length < 16 || nonce.length > 256) throw new Error("invalid_nonce");
    const createdAt = new Date(now).toISOString();
    const expiresAt = new Date(now + ttlSeconds * 1000).toISOString();
    const bindingJson = canonicalJson(binding);
    const bindingSha256 = sha256(binding);
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      this.#db.prepare(`INSERT INTO approval_requests(
        request_id,binding_json,binding_sha256,presentation_sha256,expected_principal,
        team_sha256,app_sha256,channel_sha256,thread_sha256,nonce,expires_at,state,created_at,updated_at
      ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)`).run(
        requestId, bindingJson, bindingSha256, presentationSha256, expectedPrincipal,
        teamSha256, appSha256, channelSha256, threadSha256, nonce, expiresAt, createdAt, createdAt,
      );
      this.#audit(requestId, "created", createdAt, bindingSha256, null);
      this.#db.exec("COMMIT");
    } catch (error) { this.#db.exec("ROLLBACK"); throw error; }
    return { request_id: requestId, nonce, expires_at: expiresAt, binding_sha256: bindingSha256 };
  }
  #audit(requestId, event, at, bindingSha256, eventSha256) {
    this.#db.prepare("INSERT INTO audit_events(request_id,event,at,binding_sha256,approval_event_sha256) VALUES(?,?,?,?,?)")
      .run(requestId, event, at, bindingSha256, eventSha256);
  }
  get(requestId) {
    const row = this.#db.prepare("SELECT * FROM approval_requests WHERE request_id=?").get(requestId);
    if (!row) return undefined;
    return {
      requestId: row.request_id,
      binding: JSON.parse(row.binding_json),
      bindingSha256: row.binding_sha256,
      presentationSha256: row.presentation_sha256,
      expectedPrincipal: row.expected_principal,
      teamSha256: row.team_sha256,
      appSha256: row.app_sha256,
      channelSha256: row.channel_sha256,
      threadSha256: row.thread_sha256,
      nonce: row.nonce,
      issuedAt: row.issued_at,
      expiresAt: row.expires_at,
      state: row.state,
      approvalEventId: row.approval_event_id,
      receipt: row.receipt_json ? JSON.parse(row.receipt_json) : undefined,
    };
  }
  findPendingBySlackContext({ teamSha256, appSha256, channelSha256, threadSha256 }) {
    const rows = this.#db.prepare(`SELECT request_id FROM approval_requests
      WHERE team_sha256=? AND app_sha256=? AND channel_sha256=? AND thread_sha256=? AND state='pending'`).all(
      teamSha256, appSha256, channelSha256, threadSha256,
    );
    if (rows.length !== 1) throw new Error(rows.length ? "ambiguous_slack_context" : "request_not_found");
    return this.get(rows[0].request_id);
  }
  approve(requestId, event, now) {
    exact(event, ["principal", "team_sha256", "app_sha256", "channel_sha256", "thread_sha256", "approval_event_id", "decision", "nonce"], "approval_event");
    const at = new Date(now).toISOString();
    const eventSha256 = sha256({
      principal: event.principal,
      team_sha256: event.team_sha256,
      app_sha256: event.app_sha256,
      channel_sha256: event.channel_sha256,
      thread_sha256: event.thread_sha256,
      approval_event_id: event.approval_event_id,
      decision: event.decision,
      nonce: event.nonce,
    });
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const current = this.get(requestId);
      if (!current) throw new Error("request_not_found");
      if (current.receipt) { this.#db.exec("COMMIT"); return current; }
      if (current.state === "approved_waiting_signature") { this.#db.exec("COMMIT"); return current; }
      if (current.state !== "pending") throw new Error("request_not_pending");
      if (Date.parse(current.expiresAt) <= now) {
        this.#db.prepare("UPDATE approval_requests SET state='expired',updated_at=? WHERE request_id=?").run(at, requestId);
        this.#audit(requestId, "expired", at, current.bindingSha256, null);
        this.#db.exec("COMMIT");
        throw new Error("request_expired");
      }
      if (event.principal !== current.expectedPrincipal || event.team_sha256 !== current.teamSha256 ||
          event.app_sha256 !== current.appSha256 || event.channel_sha256 !== current.channelSha256 ||
          event.thread_sha256 !== current.threadSha256 || event.decision !== "approve" || event.nonce !== current.nonce)
        throw new Error("unauthorized_decision");
      bounded(event.approval_event_id, "approval_event_id", 256);
      const update = this.#db.prepare(`UPDATE approval_requests SET
        state='approved_waiting_signature',issued_at=?,approval_event_id=?,approval_event_sha256=?,updated_at=?
        WHERE request_id=? AND state='pending'`).run(at, event.approval_event_id, eventSha256, at, requestId);
      if (update.changes !== 1) throw new Error("decision_race");
      this.#audit(requestId, "approved", at, current.bindingSha256, eventSha256);
      this.#db.exec("COMMIT");
      return this.get(requestId);
    } catch (error) {
      try { this.#db.exec("ROLLBACK"); } catch {}
      throw error;
    }
  }
  approvedWaiting() {
    return this.#db.prepare("SELECT request_id FROM approval_requests WHERE state='approved_waiting_signature'").all()
      .map((row) => row.request_id);
  }
  storeReceipt(requestId, receipt, now) {
    const current = this.get(requestId);
    if (!current) throw new Error("request_not_approved");
    if (current.receipt) return current;
    if (current.state !== "approved_waiting_signature") throw new Error("request_not_approved");
    const at = new Date(now).toISOString();
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const update = this.#db.prepare("UPDATE approval_requests SET state='issued',receipt_json=?,updated_at=? WHERE request_id=? AND state='approved_waiting_signature'")
        .run(canonicalJson(receipt), at, requestId);
      if (update.changes === 1) this.#audit(requestId, "issued", at, current.bindingSha256, sha256(current.approvalEventId));
      this.#db.exec("COMMIT");
    } catch (error) { this.#db.exec("ROLLBACK"); throw error; }
    const stored = this.get(requestId);
    if (!stored?.receipt || canonicalJson(stored.receipt) !== canonicalJson(receipt)) throw new Error("receipt_store_conflict");
    return stored;
  }
  cancel(requestId, now) {
    const at = new Date(now).toISOString();
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const current = this.get(requestId);
      if (!current) throw new Error("request_not_found");
      const update = this.#db.prepare("UPDATE approval_requests SET state='cancelled',updated_at=? WHERE request_id=? AND state='pending'")
        .run(at, requestId);
      if (update.changes !== 1) throw new Error("request_not_cancellable");
      this.#audit(requestId, "cancelled", at, current.bindingSha256, null);
      this.#db.exec("COMMIT");
      return this.get(requestId);
    } catch (error) { try { this.#db.exec("ROLLBACK"); } catch {} throw error; }
  }
  auditColumns() {
    return this.#db.prepare("PRAGMA table_info(audit_events)").all().map((row) => row.name);
  }
}

export class SlackIssuer {
  constructor({ store, signer, presenter, issuer, keyId, expectedPrincipal, now = () => Date.now() }) {
    this.store = store;
    this.signer = signer;
    this.presenter = presenter;
    this.issuer = issuer;
    this.keyId = keyId;
    this.expectedPrincipal = expectedPrincipal;
    this.now = now;
  }
  async create(request) {
    exact(request, ["approval_binding", "attestation_bundle", "ttl_seconds"], "create_request");
    const validated = validateAttestationBundle(request.attestation_bundle, { now: this.now() });
    const evidence = validated.evidence;
    const computed = bindingFromEvidence(evidence);
    if (canonicalJson(computed) !== canonicalJson(validateBinding(request.approval_binding)))
      throw new Error("presentation_binding_mismatch");
    if (!this.presenter || typeof this.presenter.post !== "function") throw new Error("presenter_unavailable");
    const now = this.now();
    if (!Number.isSafeInteger(request.ttl_seconds) || request.ttl_seconds < 1 || request.ttl_seconds > 300)
      throw new Error("invalid_ttl");
    const requestId = `sha256:${randomBytes(32).toString("hex")}`;
    const nonce = randomBytes(24).toString("base64url");
    const expiresAt = new Date(now + request.ttl_seconds * 1000).toISOString();
    const presentation = buildApprovalPresentation(evidence, computed);
    const context = await this.presenter.post({ requestId, nonce, expiresAt, evidence, binding: computed, presentation, screenshots: validated.screenshots });
    exact(context, ["team", "app", "channel", "thread"], "presented_context");
    return this.store.create({
      requestId,
      nonce,
      binding: computed,
      presentationSha256: sha256(presentation),
      expectedPrincipal: this.expectedPrincipal,
      teamSha256: sha256(bounded(context.team, "presented_team", 128)),
      appSha256: sha256(bounded(context.app, "presented_app", 128)),
      channelSha256: sha256(bounded(context.channel, "presented_channel", 128)),
      threadSha256: sha256(bounded(context.thread, "presented_thread", 128)),
      ttlSeconds: request.ttl_seconds,
      now,
    });
  }
  challenge(requestId) {
    const record = this.store.get(requestId);
    if (!record || record.state !== "pending") throw new Error("request_not_pending");
    return record.nonce;
  }
  status(requestId) {
    const record = this.store.get(requestId);
    if (!record) throw new Error("request_not_found");
    return { request_id: requestId, state: record.state, expires_at: record.expiresAt, receipt: record.receipt };
  }
  recordDecision(requestId, event) {
    return this.store.approve(requestId, event, this.now());
  }
  async completeApproved(requestId) {
    let record = this.store.get(requestId);
    if (!record || !["approved_waiting_signature", "issued"].includes(record.state)) throw new Error("request_not_approved");
    if (record.receipt) return record.receipt;
    const decision = {
      request_id: record.requestId,
      issuer: this.issuer,
      key_id: this.keyId,
      issued_at: record.issuedAt,
      expires_at: record.expiresAt,
      nonce: record.nonce,
      approver: { principal: record.expectedPrincipal, approval_event_id: record.approvalEventId },
      binding: record.binding,
    };
    const receipt = await this.signer.issue({ ...decision, decision_digest: sha256(decision) });
    record = this.store.storeReceipt(requestId, receipt, this.now());
    return record.receipt;
  }
  async approve(requestId, event) {
    const record = this.recordDecision(requestId, event);
    if (record.receipt) return record.receipt;
    return await this.completeApproved(requestId);
  }
  async recoverApproved() {
    for (const requestId of this.store.approvedWaiting()) await this.completeApproved(requestId);
  }
  cancel(requestId) { return this.store.cancel(requestId, this.now()); }
}
