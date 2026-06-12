# IPTV-API — Agent Guide

Project: **Guovin/iptv-api** v2.0.7 — Python 3.13, AGPL-3.0.

Automatic IPTV live source collector: fetches subscribe/EPG sources, speed-tests URLs, applies filter/sort/alias, writes result files, serves via Flask + nginx-rtmp.

---

## Quick commands

| Action | Command |
|--------|---------|
| Run update pipeline | `pipenv run dev` |
| Start web service only | `pipenv run service` |
| Open GUI | `pipenv run ui` |
| Build GUI executable | `pipenv run tkinter_build` |
| Build Docker multi-arch | `pipenv run docker_build` |
| Install dependencies | `pipenv install --dev` |

`Pipfile` (not `requirements.txt`). Install: `pip install pipenv` then `pipenv install --dev`.

---

## Codebase architecture

### Entry point: `main.py`

`UpdateSource` class runs 5 sequential stages:

1. **Prepare** — load channel template (`config/demo.txt`), whitelist, blacklist, aliases, frozen URLs
2. **Fetch (concurrent)** — `visit_page()` fires subscribe fetch + EPG fetch in parallel via `asyncio.gather`
3. **Aggregate** — `ResultAggregator` merges all data, writes results in real-time (every 2s by default)
4. **Speed test** — async HTTP speed test (aiohttp) on collected URLs, with resolution probing (FFmpeg), filtering by speed/resolution/location/ISP
5. **Finalize** — save cache, frozen state, notify GUI, print completion

### Directory layout

```
iptv-api/
├── main.py                 # Update pipeline orchestration
├── config/                 # Config files (not source code)
│   ├── config.ini          # All settings (env var overridable)
│   ├── demo.txt            # Channel template (categories + channel names)
│   ├── alias.txt           # Channel name aliases (re: for regex)
│   ├── subscribe.txt       # Subscribe source URLs
│   ├── epg.txt             # EPG source URLs
│   ├── whitelist.txt       # URL whitelist (bypasses filter/speed-test)
│   ├── blacklist.txt       # URL blacklist
│   ├── local.txt           # Local source URLs
│   └── logo/               # Local channel logos
├── output/                 # Generated results (gitignored)
│   ├── result.txt/m3u      # Final result files
│   ├── ipv4/ ipv6/         # Protocol-split results
│   ├── hls.*               # RTMP/HLS push results
│   ├── epg/                # EPG XML output
│   └── log/                # result.log, speed_test.log, statistic.log
├── service/
│   ├── app.py              # Flask web server (serves results, handles RTMP)
│   └── rtmp.py             # RTMP/HLS push stream management
├── utils/
│   ├── config.py           # ConfigManager singleton — loads config.ini + env overrides
│   ├── constants.py        # Path constants and regex patterns
│   ├── channel.py          # Channel data processing (1161 lines — core logic)
│   ├── aggregator.py       # ResultAggregator — real-time sorted output writer
│   ├── speed.py            # Async speed test via aiohttp
│   ├── tools.py            # 1403-line utility module — misc helpers
│   ├── types.py            # ChannelData, TestResult, CategoryChannelData typed dicts
│   ├── frozen.py           # URL freeze/thaw with exponential backoff (gzip+pickle)
│   ├── alias.py            # Channel name alias mapping (supports regex)
│   ├── whitelist.py        # URL whitelist management
│   ├── db.py               # SQLite connection pool with WAL mode
│   ├── i18n.py             # Locale loader (zh_CN / en JSON)
│   ├── retry.py            # Simple retry wrapper (2 retries)
│   ├── requests/tools.py   # HTTP requests wrapper with default UA
│   └── ffmpeg/             # FFmpeg probe + transcode wrappers
├── updates/
│   ├── subscribe/          # Subscribe source fetching (request.py)
│   └── epg/                # EPG data fetching + XML output
├── tkinter_ui/             # tkinter desktop GUI
└── locales/                # zh_CN.json, en.json
```

### Key types (`utils/types.py`)

```python
ChannelData = TypedDict(url, host, origin, ipv_type, resolution, ...)
CategoryChannelData = dict[str, dict[str, list[ChannelData]]]
  # ^ category → channel_name → list of URL entries
TestResult = TypedDict(speed, delay, resolution, ...)
```

---

## Config system

**Primary**: `config/config.ini` — INI format, `[Settings]` section only.

**Env overrides**: `ConfigManager.override_config_with_env()` checks:
- `KEY` (uppercase config key)
- `key` (lowercase)
- `Settings_KEY` / `SETTINGS_KEY` (section prefix)

Example: `open_service = True` in config → `OPEN_SERVICE=true` env var overrides it.

**Docker env vars (separate from config overrides)**:
`PUBLIC_DOMAIN`, `PUBLIC_PORT`, `NGINX_HTTP_PORT` — used in entrypoint.sh.

**ConfigManager pattern**: Singleton `config = ConfigManager()` at module level. Config properties are accessed via `@property` methods with `fallback` defaults. Also proxies `__getattr__` to raw ConfigParser for any key not explicitly defined.

**User config override**: `config/user_config.ini` overrides default `config/config.ini` if present. `config.save()` writes to `user_config.ini`.

---

## CI / GitHub Actions

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `main.yml` | `workflow_dispatch` | Runs update pipeline on Ubuntu, commits results |
| `docker-build.yml` | `workflow_dispatch` (master) | Builds + pushes Docker to Docker Hub (multi-arch: amd64/arm64/arm/v7) |
| `release.yml` | `workflow_dispatch` (master) | PyInstaller build on Windows, creates GitHub release |
| `validate-pr.yml` | `pull_request_target` | Auto-closes PRs from non-owners touching config/ or output/ |
| `upload-to-latest-release.yml` | `workflow_dispatch` | Builds + uploads artifact to latest release |
| `release-from-commit-messages.yml` | `workflow_dispatch` | Generates release from commit messages |

**Important CI note**: No `on: schedule` (cron) in `main.yml`. Forks are explicitly warned not to add cron triggers (GitHub Actions resource limits). The update workflow only runs on `workflow_dispatch`.

**Branch logic**: `main.yml` uses `GITHUB_REPOSITORY_OWNER == 'Guovin' ? 'gd' : 'master'` to select branch when checking out. Forks use `master`, upstream uses `gd`.

**Release workflows** run on `windows-latest` because they build the tkinter GUI via PyInstaller.

---

## Testing

**No test framework or test files exist.** No test directory, no pytest config, no test runner. The project has zero tests. Any test addition would be greenfield.

---

## Web service (Flask)

Serves result files at multiple endpoints. `service/app.py`:

| Route | Output |
|-------|--------|
| `/` | Default result (m3u or txt) |
| `/txt` | TXT format |
| `/m3u` | M3U format |
| `/ipv4/`, `/ipv6/` | Protocol-split results |
| `/hls*` | RTMP push results |
| `/content` | Raw interface content |
| `/log/*` | Log access |
| `/logo/<path>` | Channel logo serving |
| `/stat` | RTMP statistics |

Run with gunicorn in production: `gunicorn service.app:app -b 127.0.0.1:5180 --timeout=1000` (see `entrypoint.sh`).

---

## Docker

Multi-stage build: builder stage compiles Python deps + nginx-rtmp from source (nginx 1.27.4 + rtmp 1.2.2). Final stage is `python:3.13-alpine` with `ffmpeg` and `pcre`.

**Entrypoint** (`entrypoint.sh`):
1. Copies default config files if not present in volume
2. Substitutes nginx.conf template (port variables)
3. Starts nginx (daemon off) in background
4. Runs `main.py` (update pipeline)
5. Runs `gunicorn service.app:app` (Flask service)

**Default config on first run**: `/iptv-api-config/` is copied to `/iptv-api/config/` only if files don't already exist — preserves user volume mounts.

---

## Key implementation details

### Speed test pipeline
- Async concurrent via aiohttp (`ClientSession` with `TCPConnector`)
- Concurrent limit controlled by `speed_test_limit` (default 5)
- Per-URL timeout: `speed_test_timeout` (default 10s)
- Resolution probing via FFmpeg (`ffprobe`) when `open_filter_resolution = True`
- URL freeze system in `utils/frozen.py` — exponential backoff (base 60s, max 24h) per URL
- Host-level dedup when `speed_test_filter_host = True`
- Cache results in `output/data/cache.gz` (gzip+pickle)

### URL freeze mechanism
- `frozen.py` tracks bad URLs with exponential backoff
- `mark_url_bad(url)` increments counter, sets `frozen_until = now + backoff`
- `mark_url_good(url)` decrements counter
- `is_url_frozen(url)` checks if `frozen_until > now`
- State persisted to `output/data/frozen.gz` between runs

### Result aggregator
- Accumulates speed test results in `ResultAggregator`
- Periodic flush to disk every `write_interval` (default 2s) when `open_realtime_write = True`
- Debounce logic: min 0.2s between writes, max 2s
- Sorts by speed, applies resolution/ISP/location/ipv_type filters
- Writes result.txt, result.m3u, ipv4/ipv6 variants, statistic log

### Channel alias system
- `alias.txt` format: `primary_name, alias1, alias2, ...`
- Regex support: `re:(pattern)` prefix matches channel names via regex
- Normalized via `format_name()` — strips `-`, `_`, `()`, `[]`, spaces, HD/4K suffixes, etc.

### i18n
- JSON files in `locales/` directory
- `t("msg.key")` function returns localized string
- Falls back from configured language → `zh_CN` → empty string

---

## Style / conventions

- **No type stubs or pyi files** — inline type hints only, mostly Python 3.10+ syntax (`X | None`, `TypedDict`)
- **No formatter/linter config** found (no `.pyproject.toml`, `.ruff.toml`, `.flake8`, etc.)
- **No mypy/pyright config** — static type checking is not enforced. Type hints are present but not validated.
- **Imports**: project modules use `import utils.x` not relative imports
- **Config access**: via singleton `from utils.config import config`, then `config.open_service`, `config.app_port`, etc.
- **Single `[Settings]` section** in config.ini — no other sections used
- **Chinese + English bilingual** config.ini comments
- **`asyncio` throughout** — main pipeline, speed test, aggregator flush, scheduler all async
- **Constants as module-level path strings** in `utils/constants.py`
- **No logging framework** — uses `print()` for progress and `logging` with `RotatingFileHandler` for log files
- **Module-level singletons**: `config`, `channel_alias`, `ip_checker`

---

## Gotchas

- `pipenv run dev` runs the full update pipeline synchronously (blocks until done). The web service runs separately via `pipenv run service`.
- Config changes require restart to take effect (read once at `ConfigManager()` init).
- `open_rtmp` is auto-disabled in GitHub Actions (`os.getenv("GITHUB_ACTIONS")` check in config.py).
- Branch name auto-select logic may confuse agents: forks run on `master`, upstream runs on `gd`.
- The `validate-pr.yml` workflow auto-closes PRs that modify `config/` or `output/` from non-owners — be aware when testing.
- No test runner exists — any testing capability must be built from scratch.
- `utils/tools.py` at 1403 lines is a misc catch-all — expect utility functions there, not in dedicated modules.
