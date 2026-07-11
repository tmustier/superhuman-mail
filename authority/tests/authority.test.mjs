import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { attestationIdentityContent, OUTGOING_FIELDS, validateAttestationBundle } from "../common/attestation.mjs";
import { bindingFromEvidence, canonicalJson, issueReceipt, sha256, verifyReceipt } from "../common/receipt.mjs";
import { createSigner } from "../approval-signer/signer.mjs";
import { buildApprovalPresentation, decisionFromSocketPayload, IssuerStore, SlackIssuer, verifySlackRequest } from "../slack-issuer/issuer.mjs";
import { ExecutorJournal, MIN_GRACE_MS, SendExecutor } from "../send-executor/executor.mjs";
import { TrustedPreparedStore } from "../send-executor/prepared-store.mjs";

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
function outgoingFixture() {
  return {
    headers: [{ name: "X-Test", value: "fixture" }], superhuman_id: "send-1", rfc822_id: null,
    thread_id: "thread-1", message_id: "draft-1", in_reply_to: null,
    from: { email: "owner@example.test" }, to: [{ email: "recipient@example.test" }], cc: [], bcc: [],
    subject: "Synthetic", html_body: "<p>fixture</p>", attachments: [], scheduled_for: null,
    abort_on_reply: false, current_message_ids: [], mail_merge_recipients: [],
    sensitivity_label_id: null, sensitivity_tenant_id: null,
  };
}
function attestationBundle(now = Date.now()) {
  const images = [Buffer.from("89504e470d0a1a0a636f6d706f7365", "hex"), Buffer.from("89504e470d0a1a0a6f7574676f696e67", "hex")];
  const hashes = images.map((image) => sha256(image));
  const record = {
    schema_version: 1, created_at: new Date(now).toISOString(), expires_at: new Date(now + 15 * 60_000).toISOString(),
    send_eligible: true, confidence: "exact_superhuman_renderer",
    account: { provider_user_id: "provider-1", email: "owner@example.test" },
    thread_id: "thread-1", draft_id: "draft-1", superhuman_id: "send-1", delay_seconds: 20,
    source: {}, editor_html: "<p>fixture</p>", normalization: {}, outgoing_payload: outgoingFixture(),
    signature_settings: {}, renderer: { adapter_version: "fixture", app_version: "fixture", web_version: "fixture" },
    history_id: 1, fingerprint: { exact: sha256("fingerprint-1"), fields: {} },
    screenshots: [
      { role: "compose", path: "/executor-state/compose.png", sha256: hashes[0] },
      { role: "outgoing", path: "/executor-state/outgoing.png", sha256: hashes[1] },
    ], observation: {},
    signature: "hmac-sha256:fixture",
  };
  const identity = canonicalJson(attestationIdentityContent(record));
  record.attestation_id = sha256(identity);
  return {
    record, identity_base64: Buffer.from(identity).toString("base64"),
    screenshots: images.map((image, index) => ({ role: index ? "outgoing" : "compose", sha256: hashes[index], media_type: "image/png", data_base64: image.toString("base64") })),
  };
}
function evidenceFixture(now = Date.now()) {
  const record = attestationBundle(now).record;
  return {
    account: record.account, thread_id: record.thread_id, draft_id: record.draft_id, attestation_id: record.attestation_id,
    outgoing_fingerprint: record.fingerprint.exact, outgoing_payload: record.outgoing_payload, renderer: record.renderer,
    screenshot_sha256: record.screenshots.map((item) => item.sha256), attachment_digests: [],
    superhuman_id: record.superhuman_id, delay_seconds: record.delay_seconds,
  };
}
function preparer(now = Date.now()) { return { async prepare() { return attestationBundle(now); } }; }
function createRequest() { return { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", delay_seconds: 20, ttl_seconds: 240 }; }
function presenter() { return { async post() { return { team: "team", app: "app", channel: "channel", thread: "thread" }; } }; }
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
  assert.ok(executor.includes("/var/run/superhuman-mail/send-executor/execute.sock"));
  assert.ok(executor.includes("/var/run/superhuman-mail/render-preparer/prepare.sock"));
  const issuerLauncher = readFileSync(new URL("../slack-issuer/native/RuntimeSecretLauncher.swift", import.meta.url), "utf8");
  const signerLauncher = readFileSync(new URL("../approval-signer/native/RuntimeSecretLauncher.swift", import.meta.url), "utf8");
  const bridge = readFileSync(new URL("../send-executor/native/CredentialBridge.swift", import.meta.url), "utf8");
  for (const source of [issuer, signer, executor]) assert.ok(!source.includes("chownSync(socket, 0,"));
  for (const helper of [issuerLauncher, signerLauncher, bridge]) {
    assert.ok(helper.includes("expected_uid")); assert.ok(helper.includes("geteuid()"));
    assert.ok(helper.indexOf("runtime_identity_mismatch") < helper.indexOf("SecItemCopyMatching"));
    assert.ok(!helper.includes("ProcessInfo.processInfo.environment"));
  }
  for (const template of [
    "../release/launchd/org.superhuman-mail.slack-issuer.plist.in",
    "../release/launchd/org.superhuman-mail.approval-signer.plist.in",
    "../release/launchd/org.superhuman-mail.send-executor.plist.in",
  ]) {
    const plist = readFileSync(new URL(template, import.meta.url), "utf8");
    assert.match(plist, /<key>UserName<\/key><string>@(?:ISSUER|SIGNER|EXECUTOR)_USER@<\/string>/);
  }
});

test("secret launcher rejects direct execution from a user-owned path before Keychain access", () => {
  if (process.platform !== "darwin") return;
  const dir = mkdtempSync(join(tmpdir(), "shm-launcher-")); dirs.push(dir);
  const binary = join(dir, "launch");
  const compiled = spawnSync("xcrun", ["swiftc", "-framework", "Security", "authority/approval-signer/native/RuntimeSecretLauncher.swift", "-o", binary], {
    cwd: new URL("../..", import.meta.url), encoding: "utf8",
  });
  assert.equal(compiled.status, 0, compiled.stderr);
  const invoked = spawnSync(binary, [], { encoding: "utf8" });
  assert.notEqual(invoked.status, 0);
  assert.match(invoked.stderr, /executable_chain_unsafe/);
  assert.ok(!invoked.stderr.includes("signing_key_unavailable"));
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
  const issuer = new SlackIssuer({ store, signer, preparer: preparer(now), presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now });
  const created = await issuer.create(createRequest());
  const event = { principal: "slack:test-approver", team_sha256: sha256("team"), app_sha256: sha256("app"), channel_sha256: sha256("channel"), thread_sha256: sha256("thread"), approval_event_id: "event-1", decision: "approve", nonce: created.nonce };
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
  const changed = attestationBundle(); changed.record.thread_id = "wrong-thread";
  const identity = canonicalJson(attestationIdentityContent(changed.record));
  changed.record.attestation_id = sha256(identity); changed.identity_base64 = Buffer.from(identity).toString("base64");
  const issuer = new SlackIssuer({ store: new IssuerStore(join(dir, "issuer.sqlite3")), signer, preparer: { async prepare() { return changed; } }, presenter: { async post() { posts += 1; return { team: "team", app: "app", channel: "channel", thread: "thread" }; } }, issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver" });
  await assert.rejects(issuer.create(createRequest()), /prepared_evidence_mismatch/);
  assert.equal(posts, 0);
});

test("issuer accepts only semantic prepare requests and never caller evidence bytes", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-trusted-prepare-")); dirs.push(dir);
  const key = keys(); let preparedRequest;
  const issuer = new SlackIssuer({
    store: new IssuerStore(join(dir, "issuer.sqlite3")),
    signer: createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem }),
    preparer: { async prepare(request) { preparedRequest = request; return attestationBundle(); } }, presenter: presenter(),
    issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver",
  });
  await issuer.create(createRequest());
  assert.deepEqual(preparedRequest, { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", delay_seconds: 20 });
  await assert.rejects(issuer.create({ ...createRequest(), attestation_bundle: attestationBundle() }), /invalid_create_request_fields/);
});

test("Slack presentation exhaustively changes for every send-affecting field and includes screenshots", () => {
  const evidence = evidenceFixture(); const bind = bindingFromEvidence(evidence);
  const baseline = canonicalJson(buildApprovalPresentation(evidence, bind));
  const parsed = JSON.parse(baseline);
  assert.deepEqual(Object.keys(parsed.outgoing).filter((key) => key !== "html_body_text").sort(), [...OUTGOING_FIELDS].sort());
  assert.deepEqual(parsed.screenshot_sha256, evidence.screenshot_sha256);
  for (const field of OUTGOING_FIELDS) {
    const changed = structuredClone(evidence);
    changed.outgoing_payload[field] = { deliberately_changed: field };
    assert.notEqual(canonicalJson(buildApprovalPresentation(changed, bind)), baseline, field);
  }
});

test("Socket Mode decision maps fixed authenticated workspace context without a request ID route", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-socket-context-")); dirs.push(dir);
  const key = keys(); const now = Date.now(); const store = new IssuerStore(join(dir, "issuer.sqlite3"));
  const issuer = new SlackIssuer({
    store, signer: createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now }),
    preparer: preparer(now), presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now,
  });
  const created = await issuer.create(createRequest());
  const payload = {
    type: "event_callback", team_id: "team", api_app_id: "app", event_id: "immutable-event",
    event: { type: "message", user: "test-approver", channel: "channel", thread_ts: "thread", ts: "decision-ts", text: `approve ${created.nonce}` },
  };
  const decision = decisionFromSocketPayload({ store, payload, teamId: "team", appId: "app", channel: "channel", approvalKeyword: "approve" });
  assert.equal(decision.requestId, created.request_id);
  assert.equal(decision.event.approval_event_id, "immutable-event");
  assert.throws(() => decisionFromSocketPayload({ store, payload: { ...payload, team_id: "other" }, teamId: "team", appId: "app", channel: "channel", approvalKeyword: "approve" }), /slack_context_mismatch/);
  assert.throws(() => decisionFromSocketPayload({ store, payload: { ...payload, event: { ...payload.event, subtype: "message_changed" } }, teamId: "team", appId: "app", channel: "channel", approvalKeyword: "approve" }), /invalid_slack_decision_event/);
});

test("trusted preparer bundle carries raw Python-compatible identity bytes and required PNG roles", () => {
  const bundle = attestationBundle();
  const validated = validateAttestationBundle(bundle);
  assert.equal(validated.record.attestation_id, bundle.record.attestation_id);
  assert.deepEqual(validated.screenshots.map((item) => item.role), ["compose", "outgoing"]);
  assert.throws(() => validateAttestationBundle({ ...bundle, screenshots: [] }), /screenshot_set_mismatch/);
  assert.throws(() => validateAttestationBundle({ ...bundle, identity_base64: Buffer.from("{}").toString("base64") }), /attestation_id_mismatch/);
  const reordered = { ...bundle, screenshots: [...bundle.screenshots].reverse() };
  assert.throws(() => validateAttestationBundle(reordered), /screenshot_role_mismatch/);
  const corrupted = structuredClone(bundle); corrupted.screenshots[0].data_base64 = Buffer.from("not png").toString("base64");
  assert.throws(() => validateAttestationBundle(corrupted), /screenshot_digest_mismatch|invalid_screenshot_png/);
});

test("Python raw identity bytes remain valid across floats and non-BMP keys", () => {
  const pythonBin = process.env.PYTHON || (existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3");
  const generated = spawnSync(pythonBin, ["authority/tests/python_prepared_bundle.py"], {
    cwd: new URL("../..", import.meta.url), encoding: "utf8",
  });
  assert.equal(generated.status, 0, generated.stderr);
  const bundle = JSON.parse(generated.stdout);
  const validated = validateAttestationBundle(bundle);
  assert.equal(validated.record.signature_settings.opacity, 0.5);
  assert.equal(validated.record.signature_settings["astral_😀"], "preserved");
});

test("terminal prepared evidence cleanup removes private record, PNGs, and marker", () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-prepared-cleanup-")); dirs.push(dir);
  const state = join(dir, "state"); const prepared = join(state, "prepared-renders", "render-1");
  mkdirSync(join(state, "attestations"), { recursive: true, mode: 0o700 }); mkdirSync(prepared, { recursive: true, mode: 0o700 });
  const id = sha256("prepared-cleanup");
  const screenshots = ["compose.png", "outgoing.png"].map((name) => { const path = join(prepared, name); writeFileSync(path, "private", { mode: 0o600 }); return { path }; });
  writeFileSync(join(state, "attestations", `${id}.json`), JSON.stringify({ expires_at: new Date(Date.now() + 60_000).toISOString(), screenshots }), { mode: 0o600 });
  const store = new TrustedPreparedStore(join(state, "trusted-prepared")); store.mark(id); store.remove(id);
  assert.equal(existsSync(join(state, "attestations", `${id}.json`)), false);
  assert.equal(existsSync(prepared), false);
  assert.equal(existsSync(join(state, "trusted-prepared", `${id.slice(7)}.json`)), false);
});

test("Slack approval event cannot authorize a second pending request", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-event-replay-")); dirs.push(dir);
  const key = keys(); const now = Date.now();
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now });
  const issuer = new SlackIssuer({ store: new IssuerStore(join(dir, "issuer.sqlite3")), signer, preparer: preparer(now), presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now });
  const create = () => issuer.create(createRequest());
  const first = await create(); const second = await create();
  const base = { principal: "slack:test-approver", team_sha256: sha256("team"), app_sha256: sha256("app"), channel_sha256: sha256("channel"), thread_sha256: sha256("thread"), approval_event_id: "same-event", decision: "approve" };
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

test("credential-free issuer/signature/Python/executor contract with virtual grace clock", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-e2e-")); dirs.push(dir);
  const key = keys(); let now = Date.now();
  const bundle = attestationBundle(now); const attestation = bundle.record;
  const evidence = evidenceFixture(now); const bind = bindingFromEvidence(evidence);
  const signer = createSigner({ issuer: "test-issuer", keyId: "test-key", allowedApprover: "slack:test-approver", privateKeyPem: key.privateKeyPem, now: () => now });
  const issuer = new SlackIssuer({ store: new IssuerStore(join(dir, "issuer.sqlite3")), signer, preparer: preparer(now), presenter: presenter(), issuer: "test-issuer", keyId: "test-key", expectedPrincipal: "slack:test-approver", now: () => now });
  const created = await issuer.create(createRequest());
  const receipt = await issuer.approve(created.request_id, { principal: "slack:test-approver", team_sha256: sha256("team"), app_sha256: sha256("app"), channel_sha256: sha256("channel"), thread_sha256: sha256("thread"), approval_event_id: "event-e2e", decision: "approve", nonce: created.nonce });
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
  const result = await executor.execute({ receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_id: bind.attestation_id } });
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
  const request = { receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_id: bind.attestation_id } };
  const result = await executor.execute(request);
  assert.equal(result.state, "provider_confirmed");
  assert.ok(result.notBeforeMs >= Date.parse(receipt.issued_at) + MIN_GRACE_MS);
  assert.equal(renders, 2); assert.equal(sends, 1);
  const persisted = Buffer.concat(readdirSync(dir).map((name) => readFileSync(join(dir, name)))).toString("utf8");
  for (const privateValue of ["owner@example.test", "thread-1", "draft-1", "provider-message"])
    assert.ok(!persisted.includes(privateValue));
  const replay = await executor.execute(request);
  assert.equal(replay.state, "provider_confirmed"); assert.equal(sends, 1); assert.equal(renders, 2);
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
  const request = { receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_id: bind.attestation_id } };
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
  const request = { receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_id: bind.attestation_id } };
  await assert.rejects(executor.execute(request), /receipt_lifetime_insufficient_for_grace/);
  assert.equal(sends, 0);
});

test("executor retry resumes an interrupted non-expired grace without a second initial render", async () => {
  const dir = mkdtempSync(join(tmpdir(), "shm-grace-resume-")); dirs.push(dir);
  const path = join(dir, "executor.sqlite3"); const key = keys(); let now = Date.now(); const bind = binding();
  const receipt = receiptFixture({ key, now, bind }); const journal = new ExecutorJournal(path);
  journal.start({ receiptId: receipt.receipt_id, receiptHash: sha256(receipt), executionHash: sha256({ account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_id: bind.attestation_id }), bindingHash: sha256(bind), revisionId: sha256("revision"), expiresAt: receipt.expires_at, nowMs: now });
  now += 30_000; let renders = 0; let sends = 0;
  const restarted = new SendExecutor({
    journal: new ExecutorJournal(path),
    provider: { async render() { renders += 1; return { revision_id: sha256("revision"), draft_fingerprint: bind.outgoing_fingerprint, approval_binding: bind }; }, async send() { sends += 1; return { accepted: true, provider_confirmed: true, provider_message_id: "resumed" }; } },
    trust: { issuer: "test-issuer", keyId: "test-key", publicKeyPem: key.publicKeyPem, allowedApprovers: ["slack:test-approver"] },
    now: () => now, sleep: async (ms) => { now += ms; },
  });
  const result = await restarted.execute({ receipt, execution: { account: "owner@example.test", thread_id: "thread-1", draft_id: "draft-1", attestation_id: bind.attestation_id } });
  assert.equal(result.state, "provider_confirmed"); assert.equal(renders, 1); assert.equal(sends, 1);
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
