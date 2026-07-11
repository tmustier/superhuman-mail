#!/usr/bin/env node
import { chmodSync, chownSync, unlinkSync } from "node:fs";
import { createServer } from "node:http";
import { createSigner, readPrivateKeyFromFd } from "./signer.mjs";

const socket = process.env.SHM_SIGNER_SOCKET;
const issuer = process.env.SHM_SIGNER_ISSUER;
const keyId = process.env.SHM_SIGNER_KEY_ID;
const approver = process.env.SHM_SIGNER_APPROVER;
const callerGid = Number(process.env.SHM_SIGNER_CALLER_GID);
if (!socket || !issuer || !keyId || !approver || !Number.isSafeInteger(callerGid) || callerGid < 1)
  throw new Error("signer_configuration_required");
const signer = createSigner({ issuer, keyId, allowedApprover: approver, privateKeyPem: readPrivateKeyFromFd(0) });
try { unlinkSync(socket); } catch {}
const server = createServer((req, res) => {
  void (async () => {
    res.setHeader("content-type", "application/json");
    try {
      if (req.method !== "POST" || req.url !== "/v1/issue") throw new Error("not_found");
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 64 * 1024) throw new Error("too_large");
        chunks.push(chunk);
      }
      const receipt = signer.issue(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      res.statusCode = 200;
      res.end(JSON.stringify(receipt));
    } catch {
      res.statusCode = 400;
      res.end('{"error":"invalid_request"}');
    }
  })();
});
server.headersTimeout = 5_000;
server.requestTimeout = 5_000;
server.listen(socket, () => {
  chownSync(socket, 0, callerGid);
  chmodSync(socket, 0o660);
});
