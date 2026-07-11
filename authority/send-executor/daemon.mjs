#!/usr/bin/env node
import { constants, chmodSync, chownSync, closeSync, fstatSync, openSync, readFileSync, unlinkSync } from "node:fs";
import { createServer } from "node:http";
import { ExecutorJournal, SendExecutor } from "./executor.mjs";
import { NativeProvider } from "./provider.mjs";

const ROOT = "/Library/Application Support/superhuman-mail/send-executor";
const POLICY = `${ROOT}/trust-policy.json`;
const DB = `${ROOT}/executor.sqlite3`;
const SOCKET = "/var/run/superhuman-mail-send-executor.sock";
function readPolicy() {
  const fd = openSync(POLICY, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) throw new Error("unsafe_trust_policy");
    const value = JSON.parse(readFileSync(fd, "utf8"));
    const keys = Object.keys(value).sort().join(",");
    if (keys !== "allowedApprovers,callerGid,issuer,keyId,publicKeyPem" || !Number.isSafeInteger(value.callerGid) || value.callerGid < 1)
      throw new Error("invalid_trust_policy");
    return value;
  } finally { closeSync(fd); }
}
async function body(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > 256 * 1024) throw new Error("too_large"); chunks.push(chunk); }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}
const policy = readPolicy();
const journal = new ExecutorJournal(DB);
journal.recoverInterruptedClaims();
const executor = new SendExecutor({ journal, provider: new NativeProvider(), trust: policy });
try { unlinkSync(SOCKET); } catch {}
const server = createServer((req, res) => {
  void (async () => {
    res.setHeader("content-type", "application/json");
    try {
      if (req.method === "POST" && req.url === "/v1/execute") {
        res.statusCode = 202; res.end(JSON.stringify(await executor.execute(await body(req)))); return;
      }
      if (req.method === "GET" && req.url?.startsWith("/v1/status/")) {
        const status = executor.status(decodeURIComponent(req.url.slice(11)));
        res.statusCode = status ? 200 : 404; res.end(JSON.stringify(status || { error: "not_found" })); return;
      }
      if (req.method === "POST" && req.url?.startsWith("/v1/abort/")) {
        res.end(JSON.stringify(executor.abort(decodeURIComponent(req.url.slice(10))))); return;
      }
      throw new Error("not_found");
    } catch {
      res.statusCode = 400; res.end('{"error":"invalid_request"}');
    }
  })();
});
server.headersTimeout = 5_000;
server.requestTimeout = 190_000;
server.listen(SOCKET, () => { chownSync(SOCKET, 0, policy.callerGid); chmodSync(SOCKET, 0o660); });
