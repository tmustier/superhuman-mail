#!/usr/bin/env node
import { constants, chmodSync, chownSync, closeSync, fstatSync, lstatSync, openSync, readFileSync, unlinkSync } from "node:fs";
import { dirname } from "node:path";
import { createServer } from "node:http";
import { ExecutorJournal, SendExecutor } from "./executor.mjs";
import { AttestationImportStore } from "./imports.mjs";
import { NativeProvider } from "./provider.mjs";

const ROOT = "/Library/Application Support/superhuman-mail/send-executor";
const POLICY = "/Library/Application Support/superhuman-mail/policy/send-executor-trust.json";
const DB = `${ROOT}/executor.sqlite3`;
const IMPORTS = `${ROOT}/state/imports`;
const SOCKET = "/var/run/superhuman-mail-send-executor.sock";
function exact(value, fields, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort(); const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) throw new Error(`invalid_${label}_fields`);
}
function verifyParentChain(path) {
  let current = dirname(path);
  while (current !== dirname(current)) {
    const stat = lstatSync(current);
    if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) throw new Error("unsafe_policy_parent");
    current = dirname(current);
  }
}
function readPolicy() {
  verifyParentChain(POLICY);
  const fd = openSync(POLICY, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) throw new Error("unsafe_trust_policy");
    const value = JSON.parse(readFileSync(fd, "utf8"));
    exact(value, ["callerGid", "roots"], "trust_policy");
    if (!Number.isSafeInteger(value.callerGid) || value.callerGid < 1 || !Array.isArray(value.roots) || value.roots.length < 1 || value.roots.length > 2)
      throw new Error("invalid_trust_policy");
    const seen = new Set();
    for (const root of value.roots) {
      exact(root, ["allowedApprovers", "issuer", "keyId", "publicKeyPem"], "trust_root");
      if (typeof root.issuer !== "string" || !root.issuer || typeof root.keyId !== "string" || !root.keyId ||
          typeof root.publicKeyPem !== "string" || !Array.isArray(root.allowedApprovers) || root.allowedApprovers.length < 1)
        throw new Error("invalid_trust_root");
      const identity = `${root.issuer}\0${root.keyId}`;
      if (seen.has(identity)) throw new Error("duplicate_trust_root");
      seen.add(identity);
    }
    return value;
  } finally { closeSync(fd); }
}
async function body(req, limit) {
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > limit) throw new Error("too_large"); chunks.push(chunk); }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}
const policy = readPolicy();
const journal = new ExecutorJournal(DB);
journal.recoverInterruptedClaims();
const imports = new AttestationImportStore(IMPORTS);
const executor = new SendExecutor({ journal, provider: new NativeProvider(), trust: policy.roots });
try { unlinkSync(SOCKET); } catch {}
const server = createServer((req, res) => {
  void (async () => {
    res.setHeader("content-type", "application/json");
    try {
      if (req.method === "POST" && req.url === "/v1/import-attestation") {
        const imported = imports.import(await body(req, 56 * 1024 * 1024), { trust: policy.roots });
        res.statusCode = imported.imported ? 201 : 200; res.end(JSON.stringify(imported)); return;
      }
      if (req.method === "POST" && req.url === "/v1/execute") {
        res.statusCode = 202; res.end(JSON.stringify(await executor.execute(await body(req, 256 * 1024)))); return;
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
server.listen(SOCKET, () => { chownSync(SOCKET, process.getuid(), policy.callerGid); chmodSync(SOCKET, 0o660); });
