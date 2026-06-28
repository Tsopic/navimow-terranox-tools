# Security Policy

## Supported Use

This repository is local-first. The public repo should contain only source code,
tests, documentation, templates, and redacted examples. Keep live credentials,
captures, databases, generated viewers, screenshots, map bundles, APKs, and
decompiled app output out of git.

## Reporting Issues

Please open a GitHub issue for non-sensitive bugs, documentation gaps, and
feature requests.

Do not post secrets or private mower data in public issues, pull requests,
commit messages, screenshots, or logs. If a report requires sensitive data,
first reduce it to a shape-only reproduction or a redacted fixture. Replace
tokens, account identifiers, device IDs, MQTT topics, signed URLs, exact GPS,
and raw payload values with placeholders.

## Local Secrets Checklist

Before pushing:

```bash
git status --short
git ls-files --others --ignored --exclude-standard
```

Expected private paths include:

- `config/*.local.json`
- `.env`
- `captures/`
- `data/`
- `viewer/`
- `logs/`
- `apk/`
- `patched/`
- `decompiled/`
- `screenshots/`

If one of those appears staged, stop and unstage it before pushing.

## Write Route Boundary

Known schedule/settings command routes remain refused by the read-only clients.
Any future write support must be gated by explicit user action, tests,
documentation, rollback behavior, and a safety review.
