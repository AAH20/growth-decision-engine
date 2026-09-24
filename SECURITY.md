# Security and data handling

Do not send real customer exports, credentials, or identifying unit IDs in public issues. The CLI is local-only and makes no network requests. It reads operator-supplied files and writes a scorecard to a path the operator chooses. The scorecard contains aggregate arm/date metrics and source hashes; error messages can contain an input `unit_id`, so treat terminal logs as potentially sensitive.

For a vulnerability report, use the repository's private security advisory channel when available. Include a minimal synthetic reproducer. Do not post customer data or secrets. A future hosted deployment must add authentication, tenant isolation, encrypted storage, retention/deletion controls, audit logs, dependency review, and threat modeling before accepting production data.
