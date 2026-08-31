# Knowledge asset ledger (synthetic)

This directory is the canonical Step 27 fixture for knowledge assets. It is
intentionally separate from tickets, chat transcripts, and other customer
records. Every file is synthetic and safe to commit; it contains no production
export or real customer data.

## Publishing rules

1. Every asset must have a ledger entry in `manifest.json`. The entry owns the
   document's `owner`, `tenant_id`, `audience`, product/region/language scope,
   version, effective window, lifecycle `status`, and SHA-256 of the content.
2. A document is eligible for publication only when its owner, tenant, scope,
   version, and `effective_at` are present. `published` assets must be effective
   at the ledger's fixed `as_of` time and must not have an expired window.
3. A logical asset (`document_key`) may have only one `published` version for a
   tenant and scope. Publish a new version, then expire or withdraw the old
   one; do not edit the bytes of a published version in place.
4. `expired` and `withdrawn` assets are retained for audit/history but are not
   eligible for customer or agent answers. `draft` and `review` assets are not
   published. The validator rejects an expired window with `published` status.
5. Authorization is deterministic: a caller must match `tenant_id`, and the
   audience must allow the caller's role. `public` is customer-visible;
   `internal` is agent/supervisor-only. A model must never decide ACLs from
   document text.
6. `untrusted_content: true` marks text that may contain prompt-injection
   strings. It is data for later retrieval tests, never an instruction or a
   policy override.
7. The fixed `as_of` timestamp makes local/CI expiry tests reproducible. A
   production importer must use an explicitly recorded clock and revalidate
   the ledger before publishing.

## Lifecycle

```text
draft -> review -> published -> expired
                         \-> withdrawn
```

Withdrawal is a terminal state for that version. A correction requires a new
version and a new review/publish decision. Publication ownership and rollback
responsibility remain with the `owner` recorded in the ledger.

## Validation

The API package exposes `app.modules.knowledge.ledger.load_ledger`, which
validates metadata, file hashes, time windows, scope, and the single-published
version invariant without opening a database or calling a model:

```bash
cd apps/api
uv run pytest -q tests/test_knowledge_ledger.py
```

`data/fixtures/knowledge.json` is retained as a legacy compatibility fixture
for the earlier P0/P1 scenarios. New P2 ingestion code must read this ledger,
not infer metadata from legacy content fields.
