#!/usr/bin/env node
import { chmodSync, chownSync, readFileSync, unlinkSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { IssuerStore, SlackIssuer, verifySlackRequest } from "./issuer.mjs";
import { sha256 } from "../common/receipt.mjs";

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`missing_${name.toLowerCase()}`);
  return value;
}
function signerClient(socketPath) {
  return { issue(payload) { return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(payload));
    const req = httpRequest({ socketPath, path: "/v1/issue", method: "POST", headers: { "content-type": "application/json", "content-length": body.length } }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        try {
          if (res.statusCode !== 200) throw new Error("signer_rejected");
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch (error) { reject(error); }
      });
    });
    req.setTimeout(5_000, () => req.destroy(new Error("signer_timeout")));
    req.on("error", reject);
    req.end(body);
  }); } };
}
async function body(req, limit = 64 * 1024) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > limit) throw new Error("too_large"); chunks.push(chunk); }
  return Buffer.concat(chunks).toString("utf8");
}
const socket = required("SHM_ISSUER_SOCKET");
const signerSocket = required("SHM_SIGNER_SOCKET");
const db = required("SHM_ISSUER_DB");
const issuerName = required("SHM_ISSUER_NAME");
const keyId = required("SHM_ISSUER_KEY_ID");
const principal = required("SHM_ISSUER_APPROVER");
const approvalKeyword = required("SHM_ISSUER_APPROVAL_KEYWORD");
const approvalChannel = required("SHM_ISSUER_CHANNEL");
const callerGid = Number(required("SHM_ISSUER_CALLER_GID"));
if (!Number.isSafeInteger(callerGid) || callerGid < 1) throw new Error("invalid_caller_gid");
const secretBundle = JSON.parse(readFileSync(0, "utf8"));
if (Object.keys(secretBundle).sort().join(",") !== "bot_token,signing_secret" ||
    typeof secretBundle.signing_secret !== "string" || !secretBundle.signing_secret || secretBundle.signing_secret.length > 4096 ||
    typeof secretBundle.bot_token !== "string" || !secretBundle.bot_token || secretBundle.bot_token.length > 4096)
  throw new Error("invalid_slack_secret_bundle");
const presenter = {
  async post({ requestId, nonce, expiresAt, evidence, binding }) {
    const payload = evidence.outgoing_payload || {};
    const presentation = {
      from: payload.from,
      to: payload.to || [],
      cc: payload.cc || [],
      bcc: payload.bcc || [],
      subject: payload.subject || "",
      html_body: payload.html_body || "",
      attachments: payload.attachments || [],
      delay_seconds: evidence.delay_seconds,
      scheduled_for: payload.scheduled_for || null,
      approval_binding: binding,
    };
    const text = `Exact Superhuman send approval\nRequest: ${requestId}\nExpires: ${expiresAt}\nDecision: ${approvalKeyword} ${nonce}\n\n${JSON.stringify(presentation, null, 2)}`;
    if (text.length > 35_000) throw new Error("presentation_too_large");
    const response = await fetch("https://slack.com/api/chat.postMessage", {
      method: "POST",
      headers: { authorization: `Bearer ${secretBundle.bot_token}`, "content-type": "application/json" },
      body: JSON.stringify({ channel: approvalChannel, text, mrkdwn: false, link_names: false, unfurl_links: false, unfurl_media: false }),
      signal: AbortSignal.timeout(10_000),
    });
    const result = await response.json();
    if (!response.ok || result.ok !== true || typeof result.channel !== "string" || typeof result.ts !== "string")
      throw new Error("slack_presentation_failed");
    return { channel: result.channel, thread: result.ts };
  },
};
const issuer = new SlackIssuer({ store: new IssuerStore(db), signer: signerClient(signerSocket), presenter, issuer: issuerName, keyId, expectedPrincipal: principal });
try { unlinkSync(socket); } catch {}
const server = createServer((req, res) => {
  void (async () => {
    res.setHeader("content-type", "application/json");
    try {
      if (req.method === "POST" && req.url === "/v1/create") {
        const created = await issuer.create(JSON.parse(await body(req)));
        res.statusCode = 201; res.end(JSON.stringify(created)); return;
      }
      if (req.method === "GET" && req.url?.startsWith("/v1/status/")) {
        res.end(JSON.stringify(issuer.status(decodeURIComponent(req.url.slice(11))))); return;
      }
      if (req.method === "POST" && req.url?.startsWith("/v1/cancel/")) {
        res.end(JSON.stringify(issuer.cancel(decodeURIComponent(req.url.slice(11))))); return;
      }
      if (req.method === "POST" && req.url?.startsWith("/v1/slack/decision/")) {
        const rawBody = await body(req);
        verifySlackRequest({
          secret: secretBundle.signing_secret,
          timestamp: String(req.headers["x-slack-request-timestamp"] || ""),
          signature: String(req.headers["x-slack-signature"] || ""),
          rawBody,
        });
        const payload = JSON.parse(rawBody);
        const event = payload.event || {};
        const requestId = decodeURIComponent(req.url.slice(19));
        const nonce = issuer.challenge(requestId);
        if (event.text !== `${approvalKeyword} ${nonce}`) throw new Error("approval_keyword_mismatch");
        const receipt = await issuer.approve(requestId, {
          principal: `slack:${event.user}`,
          channel_sha256: sha256(event.channel),
          thread_sha256: sha256(event.thread_ts || event.ts),
          approval_event_id: payload.event_id || event.client_msg_id || event.ts,
          decision: "approve",
          nonce,
        });
        res.end(JSON.stringify(receipt)); return;
      }
      throw new Error("not_found");
    } catch {
      res.statusCode = 400; res.end('{"error":"invalid_request"}');
    }
  })();
});
server.headersTimeout = 5_000;
server.requestTimeout = 10_000;
server.listen(socket, () => { chownSync(socket, 0, callerGid); chmodSync(socket, 0o660); });
