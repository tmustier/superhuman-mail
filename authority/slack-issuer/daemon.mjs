#!/usr/bin/env node
import { chmodSync, chownSync, readFileSync, unlinkSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { decisionFromSocketPayload, IssuerStore, SlackIssuer } from "./issuer.mjs";
import { canonicalJson } from "../common/receipt.mjs";

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
        try { if (res.statusCode !== 200) throw new Error("signer_rejected"); resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch (error) { reject(error); }
      });
    });
    req.setTimeout(5_000, () => req.destroy(new Error("signer_timeout")));
    req.on("error", reject); req.end(body);
  }); } };
}
async function body(req, limit = 56 * 1024 * 1024) {
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > limit) throw new Error("too_large"); chunks.push(chunk); }
  return Buffer.concat(chunks).toString("utf8");
}
async function slackApi(method, payload, token) {
  const response = await fetch(`https://slack.com/api/${method}`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(payload), signal: AbortSignal.timeout(10_000),
  });
  const result = await response.json();
  if (!response.ok || result.ok !== true) throw new Error(`slack_${method}_failed`);
  return result;
}
function chunks(value, size = 28_000) {
  const result = []; for (let index = 0; index < value.length; index += size) result.push(value.slice(index, index + size));
  return result.length ? result : [""];
}
async function uploadScreenshot({ bytes, sha256: digest }, { channel, thread }, token, index) {
  const query = new URLSearchParams({ filename: `attested-render-${index + 1}.png`, length: String(bytes.length) });
  const allocatedResponse = await fetch(`https://slack.com/api/files.getUploadURLExternal?${query}`, {
    headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(10_000),
  });
  const allocated = await allocatedResponse.json();
  if (!allocatedResponse.ok || allocated.ok !== true || typeof allocated.upload_url !== "string" || typeof allocated.file_id !== "string")
    throw new Error("slack_screenshot_allocation_failed");
  const uploaded = await fetch(allocated.upload_url, {
    method: "POST", headers: { "content-type": "application/octet-stream" }, body: bytes, signal: AbortSignal.timeout(20_000),
  });
  if (!uploaded.ok) throw new Error("slack_screenshot_upload_failed");
  await slackApi("files.completeUploadExternal", {
    files: [{ id: allocated.file_id, title: `Attested render ${index + 1} — ${digest}` }],
    channel_id: channel, thread_ts: thread, initial_comment: `Screenshot digest: ${digest}`,
  }, token);
}

const socket = required("SHM_ISSUER_SOCKET");
const signerSocket = required("SHM_SIGNER_SOCKET");
const db = required("SHM_ISSUER_DB");
const issuerName = required("SHM_ISSUER_NAME");
const keyId = required("SHM_ISSUER_KEY_ID");
const principal = required("SHM_ISSUER_APPROVER");
const approvalKeyword = required("SHM_ISSUER_APPROVAL_KEYWORD");
const approvalChannel = required("SHM_ISSUER_CHANNEL");
const teamId = required("SHM_ISSUER_TEAM_ID");
const appId = required("SHM_ISSUER_APP_ID");
const callerGid = Number(required("SHM_ISSUER_CALLER_GID"));
if (!Number.isSafeInteger(callerGid) || callerGid < 1) throw new Error("invalid_caller_gid");
const secretBundle = JSON.parse(readFileSync(0, "utf8"));
if (Object.keys(secretBundle).sort().join(",") !== "app_token,bot_token" ||
    typeof secretBundle.app_token !== "string" || !secretBundle.app_token || secretBundle.app_token.length > 4096 ||
    typeof secretBundle.bot_token !== "string" || !secretBundle.bot_token || secretBundle.bot_token.length > 4096)
  throw new Error("invalid_slack_secret_bundle");

const presenter = {
  async post({ requestId, nonce, expiresAt, presentation, screenshots }) {
    const serialized = canonicalJson(presentation);
    const parts = chunks(serialized);
    const header = `Exact Superhuman send approval\nRequest: ${requestId}\nExpires: ${expiresAt}\nDecision: ${approvalKeyword} ${nonce}\nPresentation part 1/${parts.length}\n`;
    const first = await slackApi("chat.postMessage", {
      channel: approvalChannel, text: header + parts[0], mrkdwn: false, link_names: false,
      unfurl_links: false, unfurl_media: false,
    }, secretBundle.bot_token);
    if (typeof first.channel !== "string" || typeof first.ts !== "string") throw new Error("slack_presentation_failed");
    for (let index = 1; index < parts.length; index += 1) {
      await slackApi("chat.postMessage", {
        channel: first.channel, thread_ts: first.ts, text: `Presentation part ${index + 1}/${parts.length}\n${parts[index]}`,
        mrkdwn: false, link_names: false, unfurl_links: false, unfurl_media: false,
      }, secretBundle.bot_token);
    }
    for (const [index, screenshot] of screenshots.entries())
      await uploadScreenshot(screenshot, { channel: first.channel, thread: first.ts }, secretBundle.bot_token, index);
    return { team: teamId, app: appId, channel: first.channel, thread: first.ts };
  },
};
const store = new IssuerStore(db);
const issuer = new SlackIssuer({ store, signer: signerClient(signerSocket), presenter, issuer: issuerName, keyId, expectedPrincipal: principal });
void issuer.recoverApproved().catch(() => {});

function durablyRecordSocketEnvelope(envelope) {
  if (!envelope || envelope.type !== "events_api" || !envelope.payload) throw new Error("invalid_socket_envelope");
  const decision = decisionFromSocketPayload({
    store, payload: envelope.payload, teamId, appId, channel: approvalChannel, approvalKeyword,
  });
  issuer.recordDecision(decision.requestId, decision.event);
  return decision.requestId;
}
async function connectSocketMode() {
  try {
    const opened = await slackApi("apps.connections.open", {}, secretBundle.app_token);
    if (typeof opened.url !== "string" || !opened.url.startsWith("wss://")) throw new Error("invalid_socket_mode_url");
    const webSocket = new WebSocket(opened.url);
    webSocket.addEventListener("message", (message) => {
      let envelope;
      try { envelope = JSON.parse(String(message.data)); } catch { return; }
      if (typeof envelope.envelope_id !== "string") return;
      let requestId;
      try { requestId = durablyRecordSocketEnvelope(envelope); } catch {}
      webSocket.send(JSON.stringify({ envelope_id: envelope.envelope_id }));
      if (requestId) void issuer.completeApproved(requestId).catch(() => {});
    });
    webSocket.addEventListener("close", () => setTimeout(() => void connectSocketMode(), 1_000), { once: true });
    webSocket.addEventListener("error", () => webSocket.close(), { once: true });
  } catch { setTimeout(() => void connectSocketMode(), 5_000); }
}
void connectSocketMode();

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
      throw new Error("not_found");
    } catch { res.statusCode = 400; res.end('{"error":"invalid_request"}'); }
  })();
});
server.headersTimeout = 5_000; server.requestTimeout = 10_000;
server.listen(socket, () => { chownSync(socket, process.getuid(), callerGid); chmodSync(socket, 0o660); });
