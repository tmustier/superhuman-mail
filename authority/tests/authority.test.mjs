import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { bindingFromEvidence, issueReceipt, sha256, verifyReceipt } from "../common/receipt.mjs";
import { createSigner } from "../approval-signer/signer.mjs";
import { IssuerStore, SlackIssuer, verifySlackRequest } from "../slack-issuer/issuer.mjs";
import { ExecutorJournal, MIN_GRACE_MS, SendExecutor } from "../send-executor/executor.mjs";

const dirs = [];
test.afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });
function keys() {
  const pair = generateKeyPairSync("ed25519");
  return {
    privateKeyPem: pair.privateKey.export({ type: "pkcs8", format: "pem" }),
    publicKeyPem: pair.publicKey.export({ type: "spki", format: "pem" }),
  };
}
function binding(overrides = {}) {
  const h = (value) => sha256(value);
  return {
    action: "superhuman.send", provider: "superhuman",
    account_provider_user_id_sha256: h("provider-1"), account_email_sha256: h("owner@example.test"),
    thread_id_sha256: h("thread-1"), draft_id_sha256: h("draft-1"),
    attestation_id: h("attestation-1"), outgoing_fingerprint: h("fingerprint-1"),
    outgoing_payload_sha256: h("payload-1"), recipient_envelope_sha256: h("recipients-1"),
    renderer_build_sha256: h("renderer-1"), screenshot_set_sha256: h("screenshots-1"),
    send_identity_sha256: h("send-1"), delay_seconds: 20, scheduled_for_sha256: h(""),
    ...overrides,
  };
}
function evidenceFixture() {
  return {
    account: { provider_user_id: "provider-1", email: "owner@example.test" },
    thread_id: "thread-1", draft_id: "draft-1", attestation_id: sha256("attestation-1"),
    outgoing_fingerprint: sha256("fingerprint-1"),
    outgoing_payload: { from: { email: "owner@example.test" }, to: [{ email: "recipient@example.test" }], cc: [], bcc: [], subject: "Synthetic", html_body: "<p>fixture</p>", attachments: [] },
    renderer: { adapter_version: "fixture", app_version: "fixture", web_version: "fixture" },
    screenshot_sha256: [sha256("screenshot")], superhuman_id: "send-1", delay_seconds: 20,
  };
}
function presenter() { return { async post() { return { channel: "channel", thread: "thread" }; } }; }
function receiptFixture({ key, now = Date.now(), ttl = 240_000, bind = binding() }) {
  return issueReceipt({
    issuer: "test-issuer", keyId: "test-key", privateKeyPem: key.privateKeyPem,
    issuedAt: new Date(now).toISOString(), expiresAt: new Date(now + ttl).toISOString(),
    nonce: "nonce-with-enough-entropy", approver: { principal: "slack:test-approver", approval_event_id: "event-1" },
    binding: bind,
  });
}

test("service source boundaries do not combine runtime secret classes", () => {
  const issuer = readFileSync(new URL("../slack-issuer/daemon.mjs", import.meta.url), "utf8");
  const signer = readFileSync(new URL("../approval-signer/daemon.mjs", import.meta.url), "utf8");
  const executor = readFileSync(new URL("../send-executor/daemon.mjs", import.meta.url), "utf8");
  assert.ok(!/PRIVATE KEY|provider-token/.test(issuer));
  assert.ok(!/slackSecret|provider-token/.test(signer));
  assert.ok(!/slackSecret|PRIVATE KEY/.test(executor));
});

test("flat v1 signer emits a receipt accepted by the independent verifier", () => {
  const key = keys();
  const now = Date.now();
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now });
  const decision = {
    request_id: sha256("request"), issuer: "test-issuer", key_id: "test-key",
    issued_at: new Date(now).toISOString(), expires_at: new Date(now + 240_000).toISOString(), nonce: "nonce-with-enough-entropy",
    approver: { principal: "slack:test-approver", approval_event_id: "event-1" }, binding: binding(),
  };
  const receipt = signer.issue({ ...decision, decision_digest: sha256(decision) });
  const verified = verifyReceipt(receipt, { trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] }, expectedBinding: binding(), now });
  assert.equal(receipt.schema, "shm-approval-receipt/v1");
  assert.equal(verified.receiptId, receipt.receipt_id);
  assert.throws(() => verifyReceipt({ ...receipt, extra: true }, { trust: {} }), /invalid_receipt_fields/);
  const forged = { ...receipt, signature: `ed25519:${"A".repeat(86)}` };
  assert.throws(() => verifyReceipt(forged, { trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] }, now }), /forged_receipt/);
});

test("Slack issuer authenticates context, issues once, and keeps body-free audit", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-issuer-")); dirs.push(dir);
  const key = keys(); let now = Date.now();
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now });
  const store = new IssuerStore(join(dir, "issuer.sqlite3"));
  const issuer = new SlackIssuer({ store, signer, presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now });
  const evidence = evidenceFixture();
  const created = await issuer.create({ approval_binding: bindingFromEvidence(evidence), evidence, ttl_seconds: 240 });
  const event = { principal: "slack:test-approver", channel_sha256: sha256("channel"), thread_sha256: sha256("thread"), approval_event_id: "event-1", decision: "approve", nonce: created.nonce };
  const first = await issuer.approve(created.request_id, event);
  const replay = await issuer.approve(created.request_id, event);
  assert.deepEqual(replay, first);
  assert.equal(issuer.status(created.request_id).state, "issued");
  assert.deepEqual(store.auditColumns().sort(), ["approval_event_sha256", "at", "binding_sha256", "event", "request_id", "sequence"].sort());
  assert.ok(!store.auditColumns().some((name) => /body|recipient|subject|token|signature/.test(name)));
  const persisted = Buffer.concat(readdirSync(dir).map((name) => readFileSync(join(dir, name)))).toString("utf8");
  assert.ok(!persisted.includes("<p>fixture</p>"));
  assert.ok(!persisted.includes("recipient@example.test"));
});

test("issuer computes the binding from complete private evidence before Slack presentation", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-presentation-")); dirs.push(dir);
  const key = keys(); let posts = 0;
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem });
  const issuer = new SlackIssuer({ store: new IssuerStore(join(dir, "issuer.sqlite3")), signer, presenter: { async post() { posts += 1; return { channel: "channel", thread: "thread" }; } }, issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver" });
  const approved = evidenceFixture(); const changed = structuredClone(approved); changed.outgoing_payload.html_body = "<p>changed</p>";
  await assert.rejects(issuer.create({ approval_binding: bindingFromEvidence(approved), evidence: changed, ttl_seconds: 240 }), /presentation_binding_mismatch/);
  assert.equal(posts, 0);
});

test("Slack approval event cannot authorize a second pending request", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-event-replay-")); dirs.push(dir);
  const key = keys(); const now = Date.now();
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now });
  const issuer = new SlackIssuer({ store: new IssuerStore(join(dir, "issuer.sqlite3")), signer, presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now });
  const create = () => { const evidence = evidenceFixture(); return issuer.create({ approval_binding: bindingFromEvidence(evidence), evidence, ttl_seconds: 240 }); };
  const first = await create(); const second = await create();
  const base = { principal: "slack:test-approver", channel_sha256: sha256("channel"), thread_sha256: sha256("thread"), approval_event_id: "same-event", decision: "approve" };
  await issuer.approve(first.request_id, { ...base, nonce: first.nonce });
  await assert.rejects(issuer.approve(second.request_id, { ...base, nonce: second.nonce }));
});

test("Slack request verifier rejects stale or forged events", () => {
  const secret = "runtime-secret";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const rawBody = '{"event":{"text":"approve"}}';
  const signature = `v0=${createHmacCompat(secret, `v0:${timestamp}:${rawBody}`)}`;
  verifySlackRequest({ secret, timestamp, signature, rawBody });
  assert.throws(() => verifySlackRequest({ secret, timestamp, signature: `v0=${"0".repeat(64)}`, rawBody }), /invalid_slack_signature/);
});
function createHmacCompat(secret, value) {
  return (awaitImportCrypto.createHmac("sha256", secret).update(value).digest("hex"));
}
import * as awaitImportCrypto from "node:crypto";

test("credential-free issuer to signer to Python verifier to 60-second executor E2E", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-e2e-")); dirs.push(dir);
  const key = keys(); let now = Date.now();
  const attestation = {
    account: { provider_user_id: "provider-1", email: "owner@example.test" },
    thread_id: "thread-1", draft_id: "draft-1", attestation_id: sha256("attestation-1"),
    fingerprint: { exact: sha256("fingerprint-1") },
    outgoing_payload: { from: { email: "owner@example.test" }, to: [{ email: "recipient@example.test" }], cc: [], bcc: [], subject: "Synthetic", html_body: "<p>fixture</p>" },
    renderer: { adapter_version: "fixture", app_version: "fixture", web_version: "fixture" },
    screenshots: [{ sha256: sha256("screenshot") }], superhuman_id: "send-1", delay_seconds: 20,
  };
  const bind = {
    action: "superhuman.send", provider: "superhuman",
    account_provider_user_id_sha256: sha256("provider-1"), account_email_sha256: sha256("owner@example.test"),
    thread_id_sha256: sha256("thread-1"), draft_id_sha256: sha256("draft-1"),
    attestation_id: attestation.attestation_id, outgoing_fingerprint: attestation.fingerprint.exact,
    outgoing_payload_sha256: sha256(attestation.outgoing_payload),
    recipient_envelope_sha256: sha256({ from: attestation.outgoing_payload.from, to: attestation.outgoing_payload.to, cc: [], bcc: [] }),
    renderer_build_sha256: sha256({ adapter_version: "fixture", app_version: "fixture", web_version: "fixture" }),
    screenshot_set_sha256: sha256([sha256("screenshot")]), send_identity_sha256: sha256("send-1"),
    delay_seconds: 20, scheduled_for_sha256: sha256(""),
  };
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now });
  const issuer = new SlackIssuer({ store: new IssuerStore(join(dir, "issuer.sqlite3")), signer, presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now });
  const evidence = {
    account: attestation.account, thread_id: attestation.thread_id, draft_id: attestation.draft_id,
    attestation_id: attestation.attestation_id, outgoing_fingerprint: attestation.fingerprint.exact,
    outgoing_payload: attestation.outgoing_payload, renderer: attestation.renderer,
    screenshot_sha256: attestation.screenshots.map((item) => item.sha256),
    superhuman_id: attestation.superhuman_id, delay_seconds: attestation.delay_seconds,
  };
  const created = await issuer.create({ approval_binding: bind, evidence, ttl_seconds: 240 });
  const receipt = await issuer.approve(created.request_id, { principal: "slack:test-approver", channel_sha256: sha256("channel"), thread_sha256: sha256("thread"), approval_event_id: "event-e2e", decision: "approve", nonce: created.nonce });
  const publicKey = generatePublicJwk(key.publicKeyPem);
  const pythonBin = process.env.PYTHON || (existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3");
  const python = spawnSync(pythonBin, ["authority/tests/python_receipt_verify.py"], {
    cwd: new URL("../..", import.meta.url), encoding: "utf8",
    input: JSON.stringify({ receipt, attestation, issuer: "test-issuer", key_id: "test-key", approver: "slack:test-approver", public_key: publicKey.x, now_ms: now }),
  });
  assert.equal(python.status, 0, python.stderr);
  assert.equal(JSON.parse(python.stdout).receipt_id, receipt.receipt_id);
  let sends = 0;
  const executor = new SendExecutor({
    journal: new ExecutorJournal(join(dir, "executor.sqlite3")),
    provider: { async render() { return { revision_id: sha256("revision"), draft_fingerprint: bind.outgoing_fingerprint, approval_binding: bind }; }, async send() { sends += 1; return { accepted: true, provider_confirmed: true, provider_message_id: "synthetic-message" }; } },
    trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] },
    now: () => now, sleep: async (ms) => { now += ms; },
  });
  const result = await executor.execute({ receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_reference: bind.attestation_id } });
  assert.equal(result.state, "provider_confirmed"); assert.equal(sends, 1);
});
function generatePublicJwk(publicKeyPem) {
  return awaitImportCrypto.createPublicKey(publicKeyPem).export({ format: "jwk" });
}

test("executor preserves 60-second cancellable grace, rerenders, claims once, and replays status", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-executor-")); dirs.push(dir);
  const key = keys(); let now = Date.now(); let sends = 0; let renders = 0;
  const bind = binding(); const receipt = receiptFixture({ key, now, bind });
  const journal = new ExecutorJournal(join(dir, "executor.sqlite3"));
  const provider = {
    async render() { renders += 1; return { revision_id: sha256("revision"), draft_fingerprint: bind.outgoing_fingerprint, approval_binding: bind }; },
    async send() { sends += 1; return { accepted: true, provider_confirmed: true, provider_message_id: "provider-message" }; },
  };
  const executor = new SendExecutor({
    journal, provider,
    trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] },
    now: () => now, sleep: async (ms) => { now += ms; },
  });
  const request = { receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_reference: bind.attestation_id } };
  const result = await executor.execute(request);
  assert.equal(result.state, "provider_confirmed");
  assert.ok(result.notBeforeMs >= Date.parse(receipt.issued_at) + MIN_GRACE_MS);
  assert.equal(renders, 2); assert.equal(sends, 1);
  const persisted = Buffer.concat(readdirSync(dir).map((name) => readFileSync(join(dir, name)))).toString("utf8");
  for (const privateValue of ["owner@example.test", "thread-1", "draft-1", "provider-message"])
    assert.ok(!persisted.includes(privateValue));
  const replay = await executor.execute(request);
  assert.equal(replay.state, "provider_confirmed"); assert.equal(sends, 1);
  assert.ok(!journal.auditColumns().some((name) => /body|recipient|subject|token|signature|message_id$/.test(name)));
});

test("executor abort is durable during grace and prevents provider send", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-abort-")); dirs.push(dir);
  const key = keys(); const now = Date.now(); const bind = binding(); const receipt = receiptFixture({ key, now, bind });
  const journal = new ExecutorJournal(join(dir, "executor.sqlite3")); let release; let sends = 0;
  const executor = new SendExecutor({
    journal,
    provider: { async render() { return { revision_id: sha256("revision"), draft_fingerprint: bind.outgoing_fingerprint, approval_binding: bind }; }, async send() { sends += 1; return {}; } },
    trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] },
    now: () => now, sleep: () => new Promise((resolve) => { release = resolve; }),
  });
  const request = { receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_reference: bind.attestation_id } };
  const pending = executor.execute(request);
  while (!release) await new Promise((resolve) => setImmediate(resolve));
  const aborted = executor.abort(receipt.receipt_id); release();
  assert.equal(aborted.state, "aborted");
  assert.equal((await pending).state, "aborted");
  assert.equal(sends, 0);
});

test("receipt expiry at the atomic post claim fails closed after grace", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-expiry-")); dirs.push(dir);
  const key = keys(); let now = Date.now(); const bind = binding(); let sends = 0;
  const receipt = receiptFixture({ key, now, ttl: MIN_GRACE_MS, bind });
  const executor = new SendExecutor({
    journal: new ExecutorJournal(join(dir, "executor.sqlite3")),
    provider: { async render() { return { revision_id: sha256("revision"), draft_fingerprint: bind.outgoing_fingerprint, approval_binding: bind }; }, async send() { sends += 1; return {}; } },
    trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] },
    now: () => now, sleep: async (ms) => { now += ms; },
  });
  const request = { receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_reference: bind.attestation_id } };
  await assert.rejects(executor.execute(request), /receipt_expired_before_claim/);
  assert.equal(sends, 0);
});

test("executor restart converts an interrupted claimed row to truthful unknown", () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-crash-")); dirs.push(dir);
  const path = join(dir, "executor.sqlite3"); const now = Date.now();
  const journal = new ExecutorJournal(path);
  journal.start({ receiptId: sha256("receipt"), receiptHash: sha256("receipt-bytes"), executionHash: sha256("execution"), bindingHash: sha256("binding"), revisionId: sha256("revision"), expiresAt: new Date(now + 240_000).toISOString(), nowMs: now - MIN_GRACE_MS });
  journal.claim(sha256("receipt"), now);
  const restarted = new ExecutorJournal(path); restarted.recoverInterruptedClaims(new Date(now + 1).toISOString());
  assert.equal(restarted.entry(sha256("receipt")).state, "unknown");
});
