# Approval contract fixtures

`approval-binding-v1.json` is a content-free canonical flat binding fixture. Every digest is synthetic.

Valid signed receipt fixtures are generated at test runtime with ephemeral Ed25519 keys (`authority/tests/authority.test.mjs`) so this public repository does not commit private keys, production roots, principals, event IDs, deployment values, or reusable receipts. The generated receipt is independently verified against the same flat field set used by Python `superhuman_mail.approval`.
