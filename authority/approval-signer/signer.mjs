import { readFileSync } from "node:fs";
import { issueReceipt, sha256, validateBinding } from "../common/receipt.mjs";

const FIELDS = ["request_id", "issuer", "key_id", "issued_at", "expires_at", "nonce", "approver", "binding", "decision_digest"];
function exact(value, fields, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new Error(`invalid_${label}_fields`);
}
function bounded(value, label, max = 256) {
  if (typeof value !== "string" || value.length < 1 || value.length > max) throw new Error(`invalid_${label}`);
  return value;
}
export function readPrivateKeyFromFd(fd = 3) {
  const value = readFileSync(fd, "utf8");
  if (!value.includes("PRIVATE KEY") || value.length > 8192) throw new Error("invalid_signer_key");
  return value;
}
export function createSigner({ issuer, keyId, allowedApprover, privateKeyPem, now = () => Date.now() }) {
  return Object.freeze({
    issue(request) {
      exact(request, FIELDS, "sign_request");
      if (bounded(request.issuer, "issuer", 128) !== issuer || bounded(request.key_id, "key_id", 128) !== keyId)
        throw new Error("signer_identity_mismatch");
      exact(request.approver, ["principal", "approval_event_id"], "approver");
      if (bounded(request.approver.principal, "approver_principal", 128) !== allowedApprover)
        throw new Error("unauthorized_approver");
      bounded(request.approver.approval_event_id, "approval_event_id", 256);
      if (!/^sha256:[a-f0-9]{64}$/.test(bounded(request.request_id, "request_id", 128)))
        throw new Error("invalid_request_id");
      if (bounded(request.nonce, "nonce", 256).length < 16) throw new Error("invalid_nonce");
      validateBinding(request.binding);
      const decision = {
        request_id: request.request_id,
        issuer: request.issuer,
        key_id: request.key_id,
        issued_at: request.issued_at,
        expires_at: request.expires_at,
        nonce: request.nonce,
        approver: request.approver,
        binding: request.binding,
      };
      if (sha256(decision) !== request.decision_digest) throw new Error("decision_digest_mismatch");
      const current = now();
      if (Date.parse(request.issued_at) > current + 30_000 || Date.parse(request.expires_at) <= current)
        throw new Error("inactive_decision");
      return issueReceipt({
        issuer,
        keyId,
        privateKeyPem,
        issuedAt: request.issued_at,
        expiresAt: request.expires_at,
        nonce: request.nonce,
        approver: request.approver,
        binding: request.binding,
      });
    },
  });
}
