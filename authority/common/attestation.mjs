import { Buffer } from "node:buffer";
import { canonicalJson, sha256 } from "./receipt.mjs";

export const OUTGOING_FIELDS = Object.freeze([
  "headers", "superhuman_id", "rfc822_id", "thread_id", "message_id",
  "in_reply_to", "from", "to", "cc", "bcc", "subject", "html_body",
  "attachments", "scheduled_for", "abort_on_reply", "current_message_ids",
  "mail_merge_recipients", "sensitivity_label_id", "sensitivity_tenant_id",
]);
const HASH = /^sha256:[a-f0-9]{64}$/;
const MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024;
const MAX_BUNDLE_BYTES = 40 * 1024 * 1024;

function exact(value, fields, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort();
  const wanted = [...fields].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index]))
    throw new Error(`invalid_${label}_fields`);
}
function hash(value, label) {
  if (typeof value !== "string" || !HASH.test(value)) throw new Error(`invalid_${label}`);
  return value;
}

export function attestationIdentityContent(record) {
  if (!record || Array.isArray(record) || typeof record !== "object") throw new Error("invalid_attestation");
  const content = Object.fromEntries(
    Object.entries(record).filter(([key]) => !["attestation_id", "signature", "artifact_path"].includes(key)),
  );
  if (Array.isArray(content.screenshots)) {
    content.screenshots = content.screenshots.map((item) => ({ sha256: item?.sha256 }));
  }
  return content;
}

export function evidenceFromAttestation(record) {
  const payload = record.outgoing_payload;
  exact(payload, OUTGOING_FIELDS, "outgoing_payload");
  exact(record.account, ["provider_user_id", "email"], "attestation_account");
  const renderer = record.renderer;
  if (!renderer || typeof renderer !== "object") throw new Error("invalid_attestation_renderer");
  for (const key of ["adapter_version", "app_version", "web_version"])
    if (typeof renderer[key] !== "string" || !renderer[key]) throw new Error(`invalid_renderer_${key}`);
  if (!Array.isArray(record.screenshots)) throw new Error("invalid_attestation_screenshots");
  return {
    account: { provider_user_id: record.account.provider_user_id, email: record.account.email },
    thread_id: record.thread_id,
    draft_id: record.draft_id,
    attestation_id: record.attestation_id,
    outgoing_fingerprint: record.fingerprint?.exact,
    outgoing_payload: payload,
    renderer: {
      adapter_version: renderer.adapter_version,
      app_version: renderer.app_version,
      web_version: renderer.web_version,
    },
    screenshot_sha256: record.screenshots.map((item) => hash(item?.sha256, "screenshot_sha256")),
    superhuman_id: record.superhuman_id,
    delay_seconds: record.delay_seconds,
  };
}

export function validateAttestationBundle(bundle, { now = Date.now() } = {}) {
  exact(bundle, ["record", "screenshots"], "attestation_bundle");
  const record = bundle.record;
  if (!record || Array.isArray(record) || typeof record !== "object") throw new Error("invalid_attestation");
  hash(record.attestation_id, "attestation_id");
  if (sha256(canonicalJson(attestationIdentityContent(record))) !== record.attestation_id)
    throw new Error("attestation_id_mismatch");
  if (record.send_eligible !== true) throw new Error("attestation_not_send_eligible");
  const expires = Date.parse(record.expires_at);
  if (!Number.isFinite(expires) || expires <= now) throw new Error("attestation_expired");
  const evidence = evidenceFromAttestation(record);
  if (!Array.isArray(bundle.screenshots) || bundle.screenshots.length !== evidence.screenshot_sha256.length)
    throw new Error("screenshot_set_mismatch");
  let total = 0;
  const screenshots = bundle.screenshots.map((item, index) => {
    exact(item, ["sha256", "media_type", "data_base64"], `screenshot_${index}`);
    const expected = evidence.screenshot_sha256[index];
    if (hash(item.sha256, `screenshot_${index}_sha256`) !== expected) throw new Error("screenshot_order_mismatch");
    if (item.media_type !== "image/png") throw new Error("invalid_screenshot_media_type");
    if (typeof item.data_base64 !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(item.data_base64))
      throw new Error("invalid_screenshot_base64");
    const bytes = Buffer.from(item.data_base64, "base64");
    if (bytes.length < 1 || bytes.length > MAX_SCREENSHOT_BYTES || bytes.toString("base64") !== item.data_base64)
      throw new Error("invalid_screenshot_bytes");
    total += bytes.length;
    if (total > MAX_BUNDLE_BYTES) throw new Error("attestation_bundle_too_large");
    if (sha256(bytes) !== expected) throw new Error("screenshot_digest_mismatch");
    return Object.freeze({ sha256: expected, mediaType: item.media_type, bytes });
  });
  return Object.freeze({ record, evidence, screenshots });
}
