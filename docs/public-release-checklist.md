# Public Release Checklist

Use this before pushing to the public GitHub repository.

## Include

- Source code in `tools/`
- Viewer source templates in `viewer_src/`
- Tests in `tests/`
- Documentation in `README.md`, `QUICKSTART.md`, `docs/`, `DISCLAIMER.md`,
  `SECURITY.md`, and `LICENSE`
- Redacted example config files such as
  `config/navimow-live-sync.example.json`
- Docker packaging files such as `Dockerfile`, `.dockerignore`,
  `docker-compose.yml`, and `docker/entrypoint.sh`

## Exclude

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
- Any raw route payload, OAuth redirect URL, token, MQTT credential, signed URL,
  exact GPS value, device ID, or command envelope

## Commands

```bash
git status --short
git ls-files --others --exclude-standard
git ls-files --others --ignored --exclude-standard
docker build -t navimow-terranox-tools:local .
docker run --rm navimow-terranox-tools:local test
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider tests
```

Expected public files should appear in `git ls-files --others
--exclude-standard` before the first commit. Private/generated files should
appear only in ignored output or not at all.

The Docker build context should be source-only. `.dockerignore` must exclude
`.git`, captures, local configs, SQLite data, generated viewers, logs,
screenshots, APKs, and decompiled output.

## Public Messaging

The repository description and README should state that this is unofficial,
local-first, and dry-run/read-only for schedule/settings writes. Do not imply
Navimow, Segway, Ninebot, or dealer endorsement.
