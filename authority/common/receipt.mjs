import { createHash, createPrivateKey, createPublicKey, sign, timingSafeEqual, verify } from "node:crypto";

export const RECEIPT_SCHEMA = "shm-approval-receipt/v1";
export const ACTION = "superhuman.send";
export const PROVIDER = "superhuman";
export const MAX_TTL_MS = 5 * 60_000;
export const BINDING_FIELDS = Object.freeze([
  "action", "provider", "account_provider_user_id_sha256", "account_email_sha256",
  "thread_id_sha256", "draft_id_sha256", "attestation_id", "outgoing_fingerprint",
  "outgoing_payload_sha256", "recipient_envelope_sha256", "renderer_build_sha256",
  "screenshot_set_sha256", "send_identity_sha256", "delay_seconds", "scheduled_for_sha256",
]);
const RECEIPT_FIELDS = Object.freeze([
  "schema", "receipt_id", "issuer", "key_id", "issued_at", "expires_at", "nonce",
  "approver", "action", "provider", "binding", "signature",
]);
const HASH = /^sha256:[a-f0-9]{64}$/;
const SIGNATURE = /^ed25519:[A-Za-z0-9_-]{86}$/;

export function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error("noncanonical_number");
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value !== "object") throw new Error("noncanonical_value");
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}
export function sha256(value) {
  const bytes = typeof value === "string" || Buffer.isBuffer(value) ? value : canonicalJson(value);
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}
function exactKeys(value, expected, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index]))
    throw new Error(`invalid_${label}_fields`);
}
function text(value, label, max = 256) {
  if (typeof value !== "string" || value.length < 1 || value.length > max) throw new Error(`invalid_${label}`);
  return value;
}
function hash(value, label) {
  if (typeof value !== "string" || !HASH.test(value)) throw new Error(`invalid_${label}`);
  return value;
}
export function bindingFromEvidence(evidence) {
  exactKeys(evidence, [
    "account", "thread_id", "draft_id", "attestation_id", "outgoing_fingerprint",
    "outgoing_payload", "renderer", "screenshot_sha256", "attachment_digests", "superhuman_id", "delay_seconds",
  ], "approval_evidence");
  exactKeys(evidence.account, ["provider_user_id", "email"], "evidence_account");
  exactKeys(evidence.renderer, ["adapter_version", "app_version", "web_version"], "evidence_renderer");
  if (!Array.isArray(evidence.screenshot_sha256)) throw new Error("invalid_screenshot_sha256");
  evidence.screenshot_sha256.forEach((value, index) => hash(value, `screenshot_${index}`));
  if (!evidence.outgoing_payload || Array.isArray(evidence.outgoing_payload) || typeof evidence.outgoing_payload !== "object")
    throw new Error("invalid_outgoing_payload");
  const envelope = {
    from: evidence.outgoing_payload.from,
    to: evidence.outgoing_payload.to || [],
    cc: evidence.outgoing_payload.cc || [],
    bcc: evidence.outgoing_payload.bcc || [],
  };
  return validateBinding({
    action: ACTION,
    provider: PROVIDER,
    account_provider_user_id_sha256: sha256(String(evidence.account.provider_user_id || "")),
    account_email_sha256: sha256(String(evidence.account.email || "").toLowerCase()),
    thread_id_sha256: sha256(String(evidence.thread_id || "")),
    draft_id_sha256: sha256(String(evidence.draft_id || "")),
    attestation_id: hash(evidence.attestation_id, "attestation_id"),
    outgoing_fingerprint: hash(evidence.outgoing_fingerprint, "outgoing_fingerprint"),
    outgoing_payload_sha256: sha256(evidence.outgoing_payload),
    recipient_envelope_sha256: sha256(envelope),
    renderer_build_sha256: sha256(evidence.renderer),
    screenshot_set_sha256: sha256(evidence.screenshot_sha256),
    send_identity_sha256: sha256(String(evidence.superhuman_id || "")),
    delay_seconds: evidence.delay_seconds,
    scheduled_for_sha256: sha256(String(evidence.outgoing_payload.scheduled_for || "")),
  });
}

export function validateBinding(binding) {
  exactKeys(binding, BINDING_FIELDS, "binding");
  if (binding.action !== ACTION || binding.provider !== PROVIDER) throw new Error("invalid_action_provider");
  for (const key of BINDING_FIELDS) {
    if (["action", "provider", "delay_seconds"].includes(key)) continue;
    hash(binding[key], key);
  }
  if (!Number.isSafeInteger(binding.delay_seconds) || binding.delay_seconds < 0)
    throw new Error("invalid_delay_seconds");
  return Object.freeze({ ...binding });
}
export function validateReceipt(receipt) {
  exactKeys(receipt, RECEIPT_FIELDS, "receipt");
  if (receipt.schema !== RECEIPT_SCHEMA) throw new Error("invalid_receipt_schema");
  hash(receipt.receipt_id, "receipt_id");
  text(receipt.issuer, "issuer", 128);
  text(receipt.key_id, "key_id", 128);
  text(receipt.issued_at, "issued_at", 64);
  text(receipt.expires_at, "expires_at", 64);
  text(receipt.nonce, "nonce", 256);
  if (receipt.nonce.length < 16) throw new Error("invalid_nonce");
  exactKeys(receipt.approver, ["principal", "approval_event_id"], "approver");
  text(receipt.approver.principal, "approver_principal", 128);
  text(receipt.approver.approval_event_id, "approval_event_id", 256);
  if (receipt.action !== ACTION || receipt.provider !== PROVIDER) throw new Error("invalid_action_provider");
  validateBinding(receipt.binding);
  if (typeof receipt.signature !== "string" || !SIGNATURE.test(receipt.signature)) throw new Error("invalid_signature");
  const content = Object.fromEntries(Object.entries(receipt).filter(([key]) => !["receipt_id", "signature"].includes(key)));
  if (sha256(content) !== receipt.receipt_id) throw new Error("receipt_id_mismatch");
  return receipt;
}
function validLifetime(issuedAt, expiresAt, now = Date.now()) {
  const issued = Date.parse(issuedAt);
  const expires = Date.parse(expiresAt);
  if (!Number.isFinite(issued) || !Number.isFinite(expires)) throw new Error("invalid_lifetime");
  if (expires <= issued || expires - issued > MAX_TTL_MS) throw new Error("invalid_lifetime");
  if (issued > now + 30_000) throw new Error("receipt_not_yet_valid");
  if (expires <= now) throw new Error("receipt_expired");
}
export function issueReceipt({ issuer, keyId, privateKeyPem, issuedAt, expiresAt, nonce, approver, binding }) {
  validateBinding(binding);
  validLifetime(issuedAt, expiresAt);
  const content = {
    schema: RECEIPT_SCHEMA,
    issuer: text(issuer, "issuer", 128),
    key_id: text(keyId, "key_id", 128),
    issued_at: issuedAt,
    expires_at: expiresAt,
    nonce: text(nonce, "nonce", 256),
    approver: {
      principal: text(approver.principal, "approver_principal", 128),
      approval_event_id: text(approver.approval_event_id, "approval_event_id", 256),
    },
    action: ACTION,
    provider: PROVIDER,
    binding: { ...binding },
  };
  const receipt = { ...content, receipt_id: sha256(content) };
  const signature = sign(null, Buffer.from(canonicalJson(receipt)), createPrivateKey(privateKeyPem)).toString("base64url");
  return Object.freeze({ ...receipt, signature: `ed25519:${signature}` });
}
export function verifyReceipt(receipt, { trust, expectedBinding, now = Date.now() }) {
  validateReceipt(receipt);
  validLifetime(receipt.issued_at, receipt.expires_at, now);
  const roots = Array.isArray(trust) ? trust : [trust];
  const selected = roots.find((candidate) => candidate && receipt.issuer === candidate.issuer && receipt.key_id === candidate.keyId);
  if (!selected) throw new Error("untrusted_issuer");
  trust = selected;
  if (!Array.isArray(trust.allowedApprovers) || !trust.allowedApprovers.includes(receipt.approver.principal))
    throw new Error("unauthorized_approver");
  if (expectedBinding && canonicalJson(receipt.binding) !== canonicalJson(validateBinding(expectedBinding)))
    throw new Error("binding_mismatch");
  const unsigned = Object.fromEntries(Object.entries(receipt).filter(([key]) => key !== "signature"));
  const signature = Buffer.from(receipt.signature.slice("ed25519:".length), "base64url");
  const ok = verify(null, Buffer.from(canonicalJson(unsigned)), createPublicKey(trust.publicKeyPem), signature);
  if (!ok) throw new Error("forged_receipt");
  return Object.freeze({ receiptId: receipt.receipt_id, expiresAt: receipt.expires_at });
}
export function sameHash(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
