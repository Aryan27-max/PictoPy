# PictoPy — Technical Understanding

A working developer's map of this repository: what the pieces are, how they fit together, how
to start them, and where the documentation and the code disagree.

This document was written by reading the repository as the source of truth (scripts, manifests,
configuration, and application code), and by actually building and running the full stack on a
Windows 11 machine. Everything in the verification sections is a recorded result, not an
expectation.

---

## 1. What PictoPy is

PictoPy is an **offline, privacy-first desktop photo and video manager**. It indexes local
media folders and applies on-device AI to them: object detection, face detection, face
clustering ("people"), and natural-language semantic search. No media and no embeddings leave
the machine — all inference runs locally through **ONNX Runtime**.

It ships as a desktop application built with **Tauri v2** (a Rust shell hosting a webview),
with a **React 19 + TypeScript** user interface and **two separate Python FastAPI services**
doing the heavy lifting.

Licence is GPL-3.0; the project is an AOSSIE project.

### Feature surface, in terms of the code

| Capability | Where it lives |
| --- | --- |
| Object tagging (YOLOv11) | `backend/app/models/YOLO.py`, `ObjectClassifier.py` |
| Face detection + embeddings | `backend/app/models/FaceDetector.py`, `FaceNet.py` |
| Face clustering into people | `backend/app/utils/face_clusters.py` (DBSCAN, scikit-learn) |
| Semantic / text search | `backend/app/models/SigLIP2Vision.py`, `SigLIP2Text.py` |
| Video keyframe tagging | `backend/app/database/video_frames.py`, `app/utils/videos.py` |
| Albums, favourites, memories | `backend/app/routes/albums.py`, `memories.py` |
| Album sharing over the network | `backend/app/share/` + `frontend/src-tauri/src/services/tunnel.rs` |
| Filesystem watching | `sync-microservice/app/utils/watcher.py` |

---

## 2. High-level architecture

### 2.1 Four moving parts

PictoPy is **not** a single process. In development you run three or four processes at once.

1. **`frontend/src`** — React 19 + TypeScript UI, bundled by Vite, served on port **1420** in dev.
2. **`frontend/src-tauri`** — the Rust/Tauri v2 desktop shell. Owns the window, the system tray,
   OS integration, and the SSH tunnel used for album sharing.
3. **`backend`** — the main Python FastAPI service on port **52123**. Owns the SQLite database,
   all AI inference, and the media library.
4. **`sync-microservice`** — a *second*, independent Python FastAPI service on port **52124**.
   Watches registered folders on disk and tells the backend when files change.

A fifth listener exists only while an album is actively shared: the **share server** on port
**52125**, started inside the backend process.

### 2.2 Port and process map

| Port | Process | Bind address | Purpose |
| --- | --- | --- | --- |
| 1420 | Vite dev server | localhost | Dev UI, consumed by the Tauri webview |
| 52123 | `backend` (FastAPI) | localhost | Main API, database, AI |
| 52124 | `sync-microservice` (FastAPI) | localhost | Folder watcher |
| 52125 | share listener (inside backend) | **0.0.0.0** | Album sharing, on demand only |

The share port is the only socket bound outside localhost. This is deliberate and documented in
`backend/app/share/server.py`; it exists so another device on the network can fetch a shared
album. If the preferred port is busy it tries up to 5 subsequent ports (`_PORT_ATTEMPTS`),
because Windows firewall rules are per-binary rather than per-port.

### 2.3 Runtime topology

```text
                    ┌─────────────────────────────────────────┐
                    │  Tauri shell (Rust)  picto_py.exe       │
                    │  window · tray · autostart · tunnel     │
                    └───────────────┬─────────────────────────┘
                                    │ hosts a webview
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  React UI (Vite :1420 in dev)           │
                    └───┬────────────────────────┬────────────┘
              axios     │                        │  axios
        apiClient       │                        │  syncApiClient
                        ▼                        ▼
        ┌───────────────────────┐    ┌─────────────────────────┐
        │ backend  :52123       │◀───│ sync-microservice :52124│
        │ FastAPI + ONNX        │ httpx  FastAPI + watchfiles  │
        └──────────┬────────────┘    └────────────┬────────────┘
                   │                              │
                   │      both read/write         │
                   └──────────────┬───────────────┘
                                  ▼
                    SQLite: %LOCALAPPDATA%\PictoPy\PictoPy\database\PictoPy.db
```

The single shared SQLite file is the most important architectural fact in the repository: the
two Python services are independent processes with independent virtualenvs, but they are
coupled through one database file.

---

## 3. Repository structure

```text
PictoPy/
├── package.json              root scripts (setup, lint, version bump); NOT the app manifest
├── scripts/                  setup.js / setup.ps1 / setup.sh, version bump, agent hooks
├── backend/                  main FastAPI service (Python 3.12)
│   ├── main.py               app factory, router registration, table creation
│   ├── app/
│   │   ├── config/           settings.py — ports, model paths, tunables
│   │   ├── database/         SQLite access, every function prefixed db_
│   │   ├── routes/           HTTP layer (12 routers)
│   │   ├── schemas/          shared Pydantic models
│   │   ├── models/           ONNX session wrappers + MODEL_REGISTRY
│   │   ├── utils/            business logic (24 modules)
│   │   ├── share/            album share server (own FastAPI app)
│   │   └── logging/          logging setup
│   ├── tests/                pytest suite (40 files)
│   ├── requirements.txt      pinned runtime deps
│   └── PictoPy.spec          PyInstaller spec for release builds
├── sync-microservice/        second FastAPI service, own venv
│   ├── main.py
│   └── app/{config,core,database,logging,routes,schemas,utils}
├── frontend/
│   ├── package.json          the real app manifest for the UI
│   ├── vite.config.ts        dev server pinned to port 1420, strictPort
│   ├── src/                  React app (see §6)
│   └── src-tauri/            Rust desktop shell
│       ├── Cargo.toml        version source of truth for the desktop app
│       ├── tauri.conf.json   window, bundle, CSP, updater
│       ├── capabilities/     Tauri v2 permission scopes
│       └── src/{main.rs,lib.rs,services/}
├── docs/                     MkDocs site + setup guides
├── agent-kit/                playbooks and CI-gate reference for AI agents
├── publishing/               AUR packaging
└── AGENTS.md / CLAUDE.md     contributor + agent rules (per-directory)
```

Note that `package.json` at the root is **not** the application manifest. It only holds
repository-level tooling. The UI manifest is `frontend/package.json`, and the desktop app's
version comes from `frontend/src-tauri/Cargo.toml`.

---

## 4. Backend architecture (`backend/`)

### 4.1 Strict four-layer split

`backend/AGENTS.md` mandates a layering that the code actually follows:

| Layer | Path | Rule |
| --- | --- | --- |
| Route | `app/routes/<resource>.py` | HTTP only; `router = APIRouter()`; response models declared in-file |
| Schema | `app/schemas/<resource>.py` | shared Pydantic models, notably `ErrorResponse` |
| Database | `app/database/<resource>.py` | every function prefixed `db_`; rows are `TypedDict` |
| Utils | `app/utils/<domain>.py` | pure helpers, no FastAPI imports |

All database access goes through a module-private `_connect()` that sets
`PRAGMA foreign_keys = ON`. Calling `sqlite3.connect` directly is against the rules.
Responses are shaped `{success, message, data}`.

### 4.2 Startup sequence

`main.py` does a lot of work at import time and in its `lifespan` context manager, in a
deliberately ordered sequence:

1. Configure logging, create the database and thumbnail directories.
2. On `lifespan` entry, regenerate `docs/backend/backend_python/openapi.json`.
3. Create every table, **in dependency order** — clusters before faces, images and videos
   before memories.
4. `db_clear_stale_processing_flags()` — anything still flagged "busy" is leftover from a
   previous session and would otherwise block memory generation.
5. `semantic_util_sync_vocabulary()` — registers curated semantic labels as class IDs at or
   above `SEMANTIC_CLASS_ID_OFFSET`.
6. Create a single-worker `ProcessPoolExecutor` and submit three self-gating jobs: build label
   embeddings, score images, score videos. Single-worker is intentional — the scoring sweeps
   depend on the label embeddings existing first.
7. Start the SSE model-download cleanup task.

On shutdown it cancels the cleanup task, stops the share server, and shuts the executor down.

The 12 routers are mounted under `/folders`, `/albums`, `/images`, `/videos`, `/face-clusters`,
`/user-preferences`, `/memories`, `/share`, `/models`, plus an unprefixed `/shutdown` and a
`/health` endpoint defined inline.

### 4.3 Database

SQLite, at a `platformdirs` location rather than inside the repository:

```text
Windows:  %LOCALAPPDATA%\PictoPy\PictoPy\database\PictoPy.db
Linux:    ~/.local/share/PictoPy/database/PictoPy.db
```

The exception: when `GITHUB_ACTIONS=true`, `DATABASE_PATH` becomes `./test_db.sqlite3` in the
current working directory. This is the only database isolation mechanism in the repo, and it
matters for running tests locally — see §18.4.

### 4.4 AI models are not vendored

`backend/app/models/model_registry.py` holds a `MODEL_REGISTRY` of `ModelSpec` entries, each
with a URL (GitHub release `models-v1.0`), an expected SHA-256, and a size. Models are
downloaded on demand by `app/utils/model_downloader.py`, which verifies the checksum and
supports HTTP range-resume with retries.

Storage location depends on whether the process is frozen:

- Development (`sys.frozen` false): `backend/app/models/ONNX_Exports/` — git-ignored.
- Production (PyInstaller): `user_data_dir("PictoPy")/models`.

`PictoPy.spec` actively strips any `.onnx` file from the bundle, and the release workflow fails
the build if one is found. Models are a runtime download by design, never a build artifact.

Tiers (`nano` / `small` / `medium`) are selected from user preferences via
`app/utils/model_bootstrap.py`. `app/utils/hardware_detect.py` recommends a tier using direct
hardware detection (`nvidia-smi`, `psutil`) rather than by inspecting ONNX Runtime providers.

### 4.5 Album sharing

`app/share/` is a **second FastAPI app** created by `create_share_app()` and run as an asyncio
task on the backend's own event loop. Running it in-process (rather than as a thread or separate
process) guarantees that quitting PictoPy takes the share listener with it and that the
in-memory share registry cannot outlive the app. A lifecycle lock serialises start against stop
so a revoke cannot race a create.

Optionally, `frontend/src-tauri/src/services/tunnel.rs` puts an SSH tunnel in front of it to
reach beyond the local network. The Rust side is careful to shut the tunnel down on every exit
path, because an orphaned tunnel would leave an album reachable from the internet.

---

## 5. Sync-microservice architecture (`sync-microservice/`)

A deliberately separate service with its own `requirements.txt`, its own virtualenv
(`.sync-env`), and its own PyInstaller build. It mirrors the backend's layout, plus a `core/`
package for the watcher machinery.

What it does:

- On startup, waits for the shared database to be usable.
- Reads the `folders` table and starts a `watchfiles` watcher thread over those paths.
- When files appear, change, or vanish, it calls the **main backend** at
  `PRIMARY_BACKEND_URL` (`http://localhost:52123`) over `httpx` so the backend can re-index.
- Exposes `/health`, `/watcher/{status,start,stop,restart}`, `/folders/...`, and `/shutdown`.

### The startup ordering requirement

This is the least obvious operational fact in the repository. `app/core/lifespan.py` calls
`db_check_database_connection()`, which returns true only if the SQLite file opens **and the
`folders` table exists**. It retries every 5 seconds for 60 seconds and then raises
`RuntimeError`, killing the service.

Only `backend/main.py` creates the `folders` table. Therefore:

> **The backend must be started before the sync-microservice.** On a fresh machine, starting
> the sync service first will make it fail after exactly 60 seconds with "Database connection
> failed after multiple attempts".

The documented step order (backend at step 7, sync at step 8) happens to be correct, but the
docs never explain that the order is mandatory rather than cosmetic.

---

## 6. Frontend architecture (`frontend/src`)

React 19 + TypeScript, built by Vite 6, styled with Tailwind v4 and shadcn/ui.

| Concern | Location | Convention |
| --- | --- | --- |
| Redux state | `src/features/` | `<name>Slice.ts`, `<name>Selectors.ts`, `<name>Thunks.ts` |
| API URLs | `src/api/apiEndpoints.ts` | grouped `<resource>Endpoints` objects |
| API calls | `src/api/api-functions/<resource>.ts` | one typed wrapper per endpoint |
| HTTP clients | `src/api/axiosConfig.ts` | `apiClient` and `syncApiClient` |
| Types | `src/types/` | `Media.ts`, `API.ts`, … |
| Hooks | `src/hooks/` | 24 hooks |
| Components | `src/components/<Area>/` | 109 `.tsx` files |
| UI primitives | `src/components/ui/` | 27 shadcn-generated; do not hand-edit |
| Pages | `src/pages/` | one directory per screen |
| Tests | `__tests__/` beside the code | Jest + React Testing Library |

`@/` is an alias for `frontend/src`, configured in both `tsconfig.json` and `vite.config.ts`.

State is Redux Toolkit. Reducers are expected to be total — validating indices and ranges inside
the reducer rather than at the call site. Derived data belongs in a `*Selectors.ts`, not in
components. TanStack Query is also present for server-state caching.

Routing is `react-router` v7 in `src/routes/AppRoutes.tsx`. The index route is `InitialSteps`
(onboarding), which redirects to `HOME` once `currentStepIndex === -1`. A separate
`/model-manager` route is opened in its own Tauri window by the Rust `open_model_manager`
command.

Components never call `axios` directly — they go through the API wrapper layer. Adding an
endpoint always means editing two files: `apiEndpoints.ts` and the matching
`api-functions/<resource>.ts`.

TypeScript is strict (`strict`, `noUnusedLocals`, `noUnusedParameters`) and `npm run build` runs
`tsc` first, so a type error fails the build.

---

## 7. Tauri / desktop architecture (`frontend/src-tauri`)

Rust, Tauri v2.9. The binary is `picto_py`.

`tauri.conf.json` highlights:

- `beforeDevCommand: npm run dev`, `devUrl: http://localhost:1420` — so `tauri dev` starts Vite
  itself. `vite.config.ts` sets `strictPort: true`, so if 1420 is occupied the dev server fails
  loudly rather than drifting to another port.
- `frontendDist: ../dist` for production.
- `bundle.resources` maps `backend/dist/` and `sync-microservice/dist/` into
  `resources/backend` and `resources/sync-microservice`. Those directories exist in the repo
  containing only a `README.md` placeholder; they are filled by PyInstaller during release.
- A restrictive CSP that allows `connect-src` only to `localhost:52123` and `localhost:52124`
  (plus `ipc:`). **A new backend port would be blocked by CSP until this file is updated.**
- The updater plugin, with a minisign public key and a GitHub releases endpoint.

`capabilities/*.json` declares Tauri v2 permissions. A command the frontend calls must be
permitted here or it fails **at runtime, with no compile-time warning**.

### The `ci` feature — the key dev/production difference

`src/main.rs` defines `prod()` twice:

```rust
#[cfg(feature = "ci")]      fn prod(...) { /* spawns PictoPy_Server and PictoPy_Sync */ }
#[cfg(not(feature = "ci"))] fn prod(...) -> Result<(), String> { Ok(()) }
```

Without the `ci` cargo feature — which is the default, and therefore what `npm run tauri dev`
uses — **the Rust shell does not start the Python services at all**. It only opens a window.

This is why the setup guide has you run three terminals. It is a consequence of a compile-time
feature flag, not an arbitrary convention, and nothing in the documentation says so.

On shutdown, `kill_process_tree()` looks for processes named `PictoPy_Server`/`PictoPy_Sync`
and, on Windows, POSTs to each service's `/shutdown` endpoint rather than sending a signal.

A layout note: `src-tauri/AGENTS.md` states that `src/lib.rs` holds the Tauri builder and the
`invoke_handler`. It does not — `lib.rs` is one line (`pub mod services;`) and the entire
builder lives in `main.rs`. Follow `main.rs`.

---

## 8. How the components communicate

| From | To | Mechanism |
| --- | --- | --- |
| React → backend | `:52123` | `apiClient` (axios, 10s timeout), `BACKEND_URL` in `src/config/Backend.ts` |
| React → sync | `:52124` | `syncApiClient` (axios) |
| sync → backend | `:52123` | `httpx`, `PRIMARY_BACKEND_URL` in the sync service's settings |
| backend ↔ sync | SQLite file | shared database, the real coupling |
| React → Rust | Tauri IPC | `invoke()` from `@tauri-apps/api`, handlers in `main.rs` |
| Rust → services | HTTP | `/health` and `/shutdown` via `reqwest` |
| backend → models | HTTPS | one-time ONNX downloads from GitHub releases |

Every URL is hard-coded rather than environment-driven. There is **no `.env` file** anywhere in
this project — the root `.gitignore` entry for `.env` exists to ignore the backend's
*virtualenv directory*, which is confusingly also named `.env`.

Because the backend's Pydantic response models are the source of truth for frontend types, a
schema change means updating `backend/app/routes/<resource>.py`, then
`frontend/src/api/apiEndpoints.ts`, and the TypeScript types.

---

## 9. Major technologies and why they exist

| Technology | Why it is here |
| --- | --- |
| Tauri v2 (Rust) | Desktop shell far smaller than Electron; gives OS integration, tray, autostart, updater |
| React 19 + Vite | UI; Vite for fast HMR, pinned to 1420 for Tauri |
| Redux Toolkit | Cross-screen state (selection, folders, onboarding) |
| TanStack Query | Server-state caching on top of the API layer |
| Tailwind v4 + shadcn/ui | Styling with a fixed component vocabulary |
| FastAPI + Uvicorn | Both Python services; async, auto-OpenAPI |
| SQLite | Zero-config local store; no server for an offline app |
| ONNX Runtime | Single runtime for YOLOv11, FaceNet, SigLIP2 — swappable CPU/DirectML/CUDA providers |
| OpenCV, NumPy, SciPy | Image decoding, preprocessing, vector maths |
| scikit-learn | DBSCAN clustering of face embeddings into people |
| watchfiles | Efficient cross-platform filesystem watching in the sync service |
| PyInstaller | Freezes both Python services into binaries for the bundle |
| platformdirs | Correct per-OS data directory for the database, thumbnails, and models |

The provider story is worth knowing: local development installs plain CPU `onnxruntime`. The
release workflow uninstalls it and swaps in `onnxruntime-directml` on Windows and
`onnxruntime-gpu` on Linux. Model recommendation deliberately uses hardware detection instead of
querying ONNX Runtime providers, because the dev machine's providers do not reflect the
release build's.

---

## 10. Important configuration files

| File | What it controls |
| --- | --- |
| `package.json` (root) | Repo tooling: `setup`, `version:bump`, lint/format proxies, husky |
| `frontend/package.json` | UI dependencies and scripts (`dev`, `build`, `test`, `tauri`) |
| `frontend/vite.config.ts` | Port 1420, `strictPort`, `@` alias, eslint plugin, Tailwind |
| `frontend/tsconfig.json` | `strict`, `noUnusedLocals`, `noUnusedParameters` |
| `frontend/.eslintrc.json` | `no-warning-comments` = **error** for TODO/FIXME |
| `frontend/jest.config.ts`, `jest.setup.ts` | Jest + jsdom + RTL |
| `frontend/src-tauri/Cargo.toml` | Rust deps; **version source of truth** for the desktop app |
| `frontend/src-tauri/tauri.conf.json` | Window, bundle, resources, CSP, updater |
| `frontend/src-tauri/capabilities/*.json` | Tauri v2 permission scopes |
| `frontend/src-tauri/.cargo/config.toml` | 8 MB linker stack size on Windows targets |
| `backend/app/config/settings.py` | Ports, model paths, thresholds, env-var tunables |
| `backend/requirements.txt` | Pinned backend deps |
| `backend/requirements-lint.txt` | The versions CI lints with (`ruff` 0.4.10, `black` 24.4.2) |
| `backend/pyproject.toml` | Ruff `line-length = 300` — **linter only** |
| `backend/pytest.ini` | `pythonpath = .`, `testpaths = tests` |
| `.pre-commit-config.yaml` | ruff `--fix` then black |
| `.github/.markdownlint-cli2.jsonc` | MD013 off; MD024 siblings only |

### Two rules that bite

- **Never run `ruff format`.** `backend/pyproject.toml` sets Ruff's `line-length = 300` because
  Ruff is used here as a *linter only*. Formatting is black at 88 columns. Running `ruff format`
  would reflow the codebase to 300 columns and immediately fight the black pre-commit hook.
- **Never write `TODO` or `FIXME`.** ESLint's `no-warning-comments` is an error and lint runs
  with `--max-warnings 0`. One marker fails CI.

Version strings are triplicated across `package.json`, `frontend/package.json`, and
`frontend/src-tauri/Cargo.toml` and must agree. Change them only through
`npm run version:bump -- X.Y.Z`. `tauri.conf.json` has no version field in Tauri v2 — it
inherits from `Cargo.toml`.

---

## 11. Environment and dependency requirements

| Requirement | Version | Why that version |
| --- | --- | --- |
| **Python** | **3.12** | Hard requirement — see below |
| Node.js | LTS (22.x works) | Vite 6, Tauri CLI |
| Rust | stable MSVC toolchain | Tauri v2 |
| MSVC Build Tools | VS 2022 + Windows SDK | The `x86_64-pc-windows-msvc` linker |
| CMake | *not actually needed* | Installed by the script; nothing in this stack uses it |

### Why Python 3.12 specifically

`backend/requirements.txt` pins versions that have **no wheels for Python 3.13 or 3.14**:

```text
numpy==1.26.4
scipy==1.14.0
opencv-python==4.9.0.80
onnxruntime==1.17.1
scikit-learn==1.5.1
setuptools==66.1.1
```

On a machine whose only interpreter is 3.14, `pip install -r requirements.txt` cannot resolve
these and would attempt (and fail) to build them from source. All wheels installed during this
setup were `cp312`, confirming 3.12 is the real target.

Slightly inconsistently, CI's lint and PR-test jobs run **Python 3.11**, while the release
workflow and both setup scripts use **3.12**. 3.12 is the right local choice.

---

## 12. Development startup flow

Three terminals, in this order. The order matters (§5).

### Terminal 1 — backend

```powershell
cd backend
.\.env\Scripts\Activate.ps1
fastapi dev --port 52123
```

### Terminal 2 — sync-microservice

```powershell
cd sync-microservice
.\.sync-env\Scripts\Activate.ps1
fastapi dev --port 52124
```

### Terminal 3 — desktop app

```powershell
cd frontend
npm run tauri dev
```

The third command starts Vite on 1420 (via `beforeDevCommand`), compiles the Rust binary, and
opens the window. It does **not** start the Python services.

If you prefer not to activate the virtualenv, the interpreter can be invoked directly, which is
more reliable in scripts and avoids PowerShell execution-policy issues:

```powershell
.\backend\.env\Scripts\fastapi.exe dev --port 52123
.\sync-microservice\.sync-env\Scripts\fastapi.exe dev --port 52124
```

Verify the services with:

```powershell
curl.exe http://localhost:52123/health
curl.exe http://localhost:52124/health
```

---

## 13. What `npm run setup` actually does

`npm run setup` runs `cd scripts && node setup.js`. `setup.js` is only a dispatcher:

- **Windows** → `powershell.exe -ExecutionPolicy Bypass -File scripts/setup.ps1`
- **Debian/Fedora/RedHat Linux** → `scripts/setup.sh` (chmod +x first if needed)
- **Anything else (including macOS)** → prints "Unsupported operating system" and exits 1

So macOS has **no** scripted setup path despite the repo building for macOS; macOS users must
follow the manual guide.

### `scripts/setup.ps1`, step by step

1. Abort unless running as Administrator.
2. Install Chocolatey if `choco` is missing.
3. Refresh `$env:Path` from Machine + User environment variables.
4. `winget install Microsoft.VisualStudio.2022.BuildTools` with the VC++ x86/x64 tools and the
   Windows 11 SDK 22621 components.
5. Look for `cl.exe` under the BuildTools MSVC directory; prepend it to `PATH`.
6. Install Rust via `rustup-init.exe` if `rustc` is missing.
7. Install Node.js via `choco install nodejs-lts` if `node` is missing.
8. **Install Python via `choco install python312 -y`** if `python` is missing or
   `python --version` does not match `3.12`. If verification then fails, **`exit 1`**.
9. Install CMake via `choco install cmake -y` if missing.
10. Frontend: `cd frontend`, `npm install`, `cd src-tauri`, `cargo build`.
11. Backend: `cd backend`, `python -m venv .env`, activate, upgrade pip,
    `pip install -r requirements.txt`, deactivate.
12. Sync: `cd sync-microservice`, `python -m venv .sync-env`, same install sequence.

### Consequences of that ordering

Step 8 is a hard `exit 1`. **If Python installation fails, steps 9–12 never run**, so no npm
packages, no Rust build, and no virtualenvs are created. A failure at Python leaves the
repository completely unconfigured — which is exactly what happened here.

### Fragilities in the script

- **The interpreter is never pinned.** Steps 11 and 12 call bare `python -m venv`. Whichever
  `python` happens to win the `PATH` race creates the virtualenv. If that is a 3.13/3.14
  interpreter, the venv is silently wrong and `pip install` fails later with confusing
  build errors rather than a clear version message.
- **Mixed package managers.** VS Build Tools comes from winget; Python, Node, and CMake come
  from Chocolatey. A broken Chocolatey state takes down the whole setup even though winget is
  healthy.
- **Directory state depends on success.** The frontend block `Set-Location`s into
  `frontend/src-tauri` and back with `Set-Location ..\..`. Its `catch` resets to `$PSScriptRoot`
  (`scripts/`), from which the subsequent `Set-Location .\backend\` cannot resolve. A frontend
  failure therefore cascades into confusing backend/sync failures.
- `Test-Command` writes the `Get-Command` result *and* `$true` to the output stream, so it
  returns a two-element array. It happens to evaluate correctly in a boolean context, but it is
  not doing what it appears to do.
- CMake is installed but nothing in this stack consumes it. The full build was completed on this
  machine **without CMake installed at all**.

---

## 14. Windows-specific behaviour

- Administrator PowerShell is required; the script exits immediately otherwise.
- `main.py` calls `multiprocessing.freeze_support()`, required on Windows because the backend
  uses a `ProcessPoolExecutor` and is later frozen by PyInstaller.
- Shutdown on Windows is HTTP-based: `kill_process` POSTs to `/shutdown` rather than sending
  a POSIX signal.
- The share server prefers a fixed port and walks forward on conflict because Windows firewall
  rules are per-binary.
- `.cargo/config.toml` raises the linker stack to 8 MB for `x86_64-pc-windows-msvc`.
- `onnxruntime` 1.17.1 prints `UserWarning: Unsupported Windows version (11)`. This is a
  cosmetic version-parsing bug in that ONNX Runtime release. It is harmless — the CPU provider
  loads and all 1109 backend tests pass with it.
- The setup guide's advice to "press Enter if the script seems stuck for 10 minutes" is a
  symptom of PowerShell's QuickEdit mode pausing console output when the window is clicked, not
  of the build actually hanging.

---

## 15. The setup failure encountered on this machine

### 15.1 What was observed

`npm run setup` reached the Python step and died:

```text
Installing Python 3.12...
Chocolatey v2.5.0
[NuGet] One or more unresolved package dependency constraints detected in the Chocolatey lib folder.
  'python3 3.14.2 constraint: python314 (= 3.14.2)'
Unable to resolve dependency 'kb2919442': Unable to find a version of 'kb2919442' that is
compatible with 'KB2919355 1.0.20160915 constraint: kb2919442 (>= 1.0.20160915)'.

Chocolatey installed 0/1 packages. 1 packages failed.
Failed to install Python 3.12. Please check your installation.
Setup finished with exit code 1
```

Everything before it succeeded: Chocolatey present, VS Build Tools installed via winget,
`cl.exe` found, Rust 1.93.1 and Node v22.23.1 already installed.

### 15.2 Root cause

Two independent problems, both **outside** the PictoPy repository:

**Problem 1 — the machine had no Python 3.12.** The only interpreter present was Python 3.14.6.
The script's check `python --version -match "3\.12"` correctly detected this and tried to
install 3.12.

**Problem 2 — Chocolatey's local package database was internally inconsistent**, so it could
not install *anything*. Inspecting the installed Chocolatey packages showed:

```text
python|3.14.2
python3|3.14.2
python314|3.14.6
```

The `python3` meta-package at 3.14.2 declares an exact-version dependency
`python314 (= 3.14.2)`, but the installed `python314` is **3.14.6**. That constraint can never
be satisfied. NuGet refuses to perform *any* install into a lib folder with unresolved
constraints, so `choco install python312` was rejected before it began. The secondary
`kb2919442` error comes from the legacy `KB2919355` dependency chain that Chocolatey's Python
packages still carry; that package cannot be resolved from the configured feed.

In short: a previous partial Python upgrade left Chocolatey wedged. PictoPy's setup script has
no fallback for this and treats it as fatal.

### 15.3 How it was resolved

Rather than attempt to repair Chocolatey's package database — which is invasive, affects
software unrelated to this project, and risks breaking the user's existing Python 3.14 — Python
3.12 was installed with the **Python Install Manager** (`pymanager`, version 26.3), which was
already present on the machine and is Python's own current installer:

```powershell
py install 3.12
```

This installed **Python 3.12.10** side by side with 3.14.6, touching neither Chocolatey nor the
existing interpreter, and requiring no administrator rights.

The virtualenvs were then created with the interpreter **explicitly pinned**, removing the
`PATH`-race fragility described in §13:

```powershell
py -V:3.12 -m venv .env
```

No repository file was modified to achieve any of this.

---

## 16. What was actually done on this machine

The setup script was not re-run. Its remaining steps were performed directly and in the correct
order, with the interpreter pinned.

```powershell
# 1. Python 3.12, bypassing the broken Chocolatey state
py install 3.12                      # -> Python 3.12.10

# 2. Backend virtualenv
cd C:\Users\gupta\PictoPy\backend
py -V:3.12 -m venv .env
.\.env\Scripts\python.exe -m pip install --upgrade pip
.\.env\Scripts\python.exe -m pip install -r requirements.txt

# 3. Sync-microservice virtualenv
cd ..\sync-microservice
py -V:3.12 -m venv .sync-env
.\.sync-env\Scripts\python.exe -m pip install --upgrade pip
.\.sync-env\Scripts\python.exe -m pip install -r requirements.txt

# 4. Frontend dependencies
cd ..\frontend
npm install

# 5. Rust desktop shell
cd src-tauri
cargo build
```

### Verification results

Every line below is a recorded result from this machine.

| Check | Command | Result |
| --- | --- | --- |
| Python version | `py -V:3.12 --version` | Python 3.12.10 |
| Backend venv | `.env\Scripts\python.exe --version` | Python 3.12.10 |
| Backend heavy deps | import onnxruntime/numpy/cv2/sklearn/scipy | OK — onnxruntime 1.17.1, numpy 1.26.4, cv2 4.9.0 |
| ONNX providers | `onnxruntime.get_available_providers()` | `['AzureExecutionProvider', 'CPUExecutionProvider']` |
| Sync venv | import fastapi/uvicorn/watchfiles/loguru | OK, Python 3.12.10 |
| Frontend deps | `npm install` | 899 packages, exit 0 |
| Rust build | `cargo build` | Finished dev profile in **5m 24s**, `picto_py.exe` (30.3 MB) |
| Backend health | `GET :52123/health` | `200 {"message":"PictoPy Server is up and running!"}` |
| Database created | `%LOCALAPPDATA%\PictoPy\PictoPy\database\PictoPy.db` | created, 340 KB, all tables |
| Sync health | `GET :52124/health` | `200 {"status":"healthy","database":"connected","watcher":"stopped"}` |
| Watcher status | `GET :52124/watcher/status` | `{"is_running":false,"folders_count":0,...}` — correct with no folders registered |
| Vite dev server | `GET :1420` | 200 |
| Desktop app | `npm run tauri dev` | `picto_py.exe` running, window title **"PictoPy"** |
| Backend tests | `pytest` | **1109 passed** in 65 s |
| Frontend tests | `npm test` | **41 suites, 377 tests passed** in 93 s |
| Frontend lint | `npm run lint:check` | exit 0, clean |
| Rust format | `cargo fmt -- --check` | exit 0, clean (after installing `rustfmt`, §18.11) |
| Markdown | `markdownlint-cli2` on repo files | 68 files, 0 errors (with venvs excluded, §18.10) |
| Frontend format | `npm run format:check` | **fails on 260 files — line endings only, not a real defect (§18.12)** |

All four processes were confirmed healthy **simultaneously**: backend, sync-microservice, Vite,
and the desktop window.

### End-to-end confirmation

Beyond the health checks, the application was exercised through its own UI. After a folder was
added via the desktop app, the sync service picked it up on its own:

```json
{"is_running":true,"folders_count":1,"thread_alive":true,"thread_id":28444,
 "watched_folders":[{"id":"5540e54e-…","path":"C:\\Users\\…\\Desktop\\gej"}]}
```

That single response proves the whole chain works: the UI called the backend, the backend wrote
the `folders` table, and the independent sync-microservice read that table and started a live
`watchfiles` watcher thread on the path. `watcher: stopped` with `folders_count: 0` is simply
the correct idle state before any folder is registered.

### Repository changes

**None.** `git status` is clean. Two files appeared modified during verification and were
restored:

- `docs/backend/backend_python/openapi.json` — the backend **rewrites this on every startup**
  (`generate_openapi_json()` in the `lifespan` handler). The regenerated content differed from
  the committed file, which means the committed spec is stale relative to the code (it is
  missing, for example, the `password` field on the share-create request). Expect this file to
  show as modified every time you run the backend; it is not something you changed.
- `frontend/src-tauri/Cargo.toml` — line-ending normalisation only (`git diff --numstat`
  reported `0 0`). No content change.

---

## 17. Reproducing this environment from scratch

For a Windows machine, ignoring the setup script:

```powershell
# Prerequisites (skip any already present)
winget install Microsoft.VisualStudio.2022.BuildTools --override `
  "--wait --passive --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows11SDK.22621"
winget install Rustlang.Rustup
winget install OpenJS.NodeJS.LTS
py install 3.12          # or: winget install Python.Python.3.12

# Project
cd <repo>\backend
py -V:3.12 -m venv .env
.\.env\Scripts\python.exe -m pip install -r requirements.txt

cd ..\sync-microservice
py -V:3.12 -m venv .sync-env
.\.sync-env\Scripts\python.exe -m pip install -r requirements.txt

cd ..\frontend
npm install
```

Then start the three processes in the order given in §12. CMake is not required.

If you would rather use the script, install Python 3.12 first by any working method; the script
detects it and skips its own Chocolatey step.

---

## 18. Setup pitfalls

### 18.1 A Python version other than 3.12 fails late and confusingly

The pinned `numpy`/`scipy`/`opencv`/`onnxruntime` versions have no wheels beyond 3.12. With a
newer interpreter pip attempts source builds and fails deep in a compiler log rather than saying
"wrong Python version".

### 18.2 The virtualenv directory is called `.env`

`backend/.env` is a **virtualenv directory**, not a dotenv file. The root `.gitignore` entry
`.env` exists to ignore it. This project has no dotenv configuration at all; the sync service's
env is `.sync-env`.

### 18.3 The sync service must start after the backend

It exits after 60 seconds if the `folders` table does not yet exist. See §5.

### 18.4 `pytest` writes to your real development database

`backend/tests/conftest.py` creates every table but performs **no database isolation**.
`DATABASE_PATH` only diverts to a local `test_db.sqlite3` when `GITHUB_ACTIONS=true`. Running
`pytest` locally without that variable operates on
`%LOCALAPPDATA%\PictoPy\PictoPy\database\PictoPy.db` — your real library.

Run tests isolated, the way CI does:

```powershell
cd backend
$env:GITHUB_ACTIONS = "true"
.\.env\Scripts\python.exe -m pytest
```

That is how the 1109-test run in §16 was performed, leaving the development database untouched.

### 18.5 Running the backend dirties your working tree

`generate_openapi_json()` rewrites `docs/backend/backend_python/openapi.json` on every startup.
Do not commit that noise unless updating the spec is the point of your change.

### 18.6 Port 1420 is strict

`strictPort: true` means `tauri dev` fails rather than falling back if 1420 is taken.

### 18.7 `tauri dev` recompiles after a plain `cargo build`

The Tauri CLI builds with its own injected environment, so the first `npm run tauri dev` after a
bare `cargo build` recompiles a substantial part of the tree. This is expected, not a broken
cache.

### 18.8 A new backend port needs a CSP change

`tauri.conf.json`'s `connect-src` lists only 52123 and 52124. A new service port is blocked in
the webview until that CSP is updated.

### 18.9 The first run downloads AI models

Nothing is vendored. The first indexing run fetches ONNX models from GitHub releases into
`backend/app/models/ONNX_Exports/`. An offline first run will not tag anything.

### 18.10 Creating the virtualenvs breaks the local markdown lint gate

`.github/.markdownlint-cli2.jsonc` excludes `**/venv/**` and `**/.venv/**`, but **not**
`**/.env/**` or `**/.sync-env/**` — the exact directory names the setup script creates. Once the
virtualenvs exist, the documented command lints third-party licence and model-card files inside
`site-packages`:

```text
Linting: 97 file(s)
Summary: 52 error(s)
```

Every one of those 52 errors comes from `backend/.env/Lib/site-packages/...` or
`sync-microservice/.sync-env/Lib/site-packages/...`. The repository's own markdown is clean: with
`!**/.env/**` and `!**/.sync-env/**` added, the same command lints 68 files with 0 errors.

CI does not hit this because the lint job installs from `backend/requirements-lint.txt` with
plain pip and never creates a virtualenv at those paths. Locally, add the two exclusions (or
lint an explicit file list) to get a meaningful result.

### 18.11 `rustup` does not install `rustfmt`, so the Rust format gate cannot run

`scripts/setup.ps1` installs Rust with `rustup-init.exe -y`, which does not add the `rustfmt`
component. The CI gate `cargo fmt -- --check` then fails locally with:

```text
error: 'cargo-fmt.exe' is not installed for the toolchain 'stable-x86_64-pc-windows-msvc'
```

This is a missing prerequisite, not a formatting problem. Fix it once:

```powershell
rustup component add rustfmt
```

After that the gate passes cleanly. This was installed during this setup.

### 18.12 `npm run format:check` fails on ~260 unmodified files on Windows

On a fresh Windows clone, `npm run format:check` reports:

```text
[warn] Code style issues found in 260 files. Run Prettier with --write to fix.
```

**None of those files have a real formatting problem.** Two independent checks prove it:

- `npx prettier --check --end-of-line auto <file>` reports "All matched files use Prettier code
  style!" for the same files.
- `git diff --ignore-cr-at-eol` produces **no output at all**, so the only byte-level difference
  anywhere is the carriage return at end of line.

The cause is that Git for Windows defaults to `core.autocrlf=true` and **this repository has no
`.gitattributes`**, so line endings are converted on checkout while Prettier's default
`endOfLine` is `"lf"`. Linux CI checks out with LF and never sees it.

The durable fix belongs in the repository — either a `.gitattributes` containing
`* text=auto eol=lf`, or `"endOfLine": "auto"` in `frontend/.prettierrc`. Both are project
changes that should go through a PR, so **neither was applied here**; the working tree was left
exactly as committed.

Until then, check formatting on Windows with the flag, which reflects what CI will conclude:

```powershell
cd frontend
npx prettier --check --end-of-line auto "**/*.{ts,tsx,json}"
```

Be careful with `npm run format:fix` on Windows: it rewrites all 260 files to LF, which then
shows up as 260 modified files in `git status` even though no content changed.

---

## 19. Documentation versus repository reality

Discrepancies found by comparing the docs to the code. None of these blocked the setup, but all
of them cost time.

| # | Documentation says | Repository actually does |
| --- | --- | --- |
| 1 | `npm run linux-dev` runs `bash ./scripts/linux-dev.sh` | **`scripts/linux-dev.sh` does not exist.** The script is broken. |
| 2 | `npm run win-dev` runs `scripts/win-dev.bat` | **`scripts/win-dev.bat` does not exist.** The script is broken. |
| 3 | `npm run cargo:check` runs `cd backend && cargo check` | `backend/` is Python and has **no `Cargo.toml`**. Presumably meant `frontend/src-tauri`. |
| 4 | `Manual_Setup_Guide.md` requires **Miniconda** and `conda create -p .env python=3.12` | The setup script uses plain `python -m venv`. Two different toolchains produce the same `.env` directory; the script path needs no conda. |
| 5 | "ruff, black, mypy … should already be installed as they are included in requirements.txt" | `requirements.txt` contains `ruff`, `black`, `pre-commit`, `pytest` — but **not `mypy`**. There is also no mypy gate in CI. |
| 6 | Setup installs Python **3.12** | CI's lint and PR-test jobs run Python **3.11**; the release workflow uses 3.12. |
| 7 | `src-tauri/AGENTS.md`: "`src/lib.rs` — the Tauri builder … where every command is registered" | `lib.rs` is one line (`pub mod services;`). The builder and `invoke_handler` are in `main.rs`. |
| 8 | `backend/run-server.ps1` / `run.sh` are present as run helpers | They launch **hypercorn on port 8000**, not `fastapi dev` on 52123. Stale; do not use them for development. |
| 9 | Setup steps list backend then sync | Correct, but the docs never say the order is **mandatory** (§5). |
| 10 | Setup script installs CMake as a prerequisite | Nothing in the build uses it; the entire stack built here with **CMake absent**. |
| 11 | The setup script handles Windows/Debian/Fedora | macOS has **no** scripted path at all, despite macOS release builds existing. |
| 12 | — (unstated) | `npm run tauri dev` does **not** start the Python services; that requires the non-default `ci` cargo feature (§7). |
| 13 | — (unstated) | Running the backend rewrites a tracked file, `docs/backend/backend_python/openapi.json`, and the committed copy is stale. |
| 14 | — (unstated) | `pytest` uses your real database unless `GITHUB_ACTIONS=true` (§18.4). |
| 15 | The markdown gate excludes virtualenvs (`venv`, `.venv`) | It does **not** exclude `.env` or `.sync-env`, the names the setup script creates, so the documented lint command fails locally with 52 errors from `site-packages` (§18.10). |
| 16 | `cargo fmt -- --check` is a CI gate you should run before pushing | The setup script's `rustup-init -y` never installs the `rustfmt` component, so the command errors out until you run `rustup component add rustfmt` (§18.11). |
| 17 | `npm run format:check` verifies formatting | On Windows it fails on ~260 unmodified files. The repo has no `.gitattributes`, so `core.autocrlf=true` gives the working tree CRLF while Prettier expects LF (§18.12). |

---

## 20. Important commands

Run these from the repository root. Each `cd` is wrapped so it does not leak.

| Task | Check (what CI runs) | Fix |
| --- | --- | --- |
| Frontend lint | `(cd frontend && npm run lint:check)` | `npm run lint:fix` |
| Frontend format | `(cd frontend && npm run format:check)` | `npm run format:fix` |
| Frontend tests | `(cd frontend && npm test)` | — |
| Python lint + format | `pre-commit run --config .pre-commit-config.yaml --all-files` | rewrites in place |
| Backend tests | `(cd backend && pytest)` | — |
| Rust format | `(cd frontend/src-tauri && cargo fmt -- --check)` | `cargo fmt` |
| Markdown | `npx markdownlint-cli2@0.22.1 --config .github/.markdownlint-cli2.jsonc` | — |
| Agent hook tests | `node scripts/agent-format-hook.test.mjs` | — |
| Version bump | `npm run version:bump -- X.Y.Z` | — |

The markdownlint version is pinned deliberately to match `markdownlint-cli2-action@v23` used by
CI; bare `npx markdownlint-cli2` resolves to the latest release and can disagree.

Install linters from `backend/requirements-lint.txt` so your versions match the runner's.

`agent-kit/references/ci-gates.md` maps every gate to its workflow job, and
`agent-kit/skills/pre-pr-check/` runs all of them in order.

---

## 21. Contribution rules worth knowing before you start

These are enforced by the project and are easy to trip over:

- **Do not open a PR for an issue maintainers have not reviewed and labelled.** This is the
  project's most-enforced process rule.
- PRs target `main`, and the body must reference its issue as `#<number>` —
  `.github/workflows/linked-issue.yml` copies labels from the issue onto the PR.
- Commit subjects are short and imperative, prefixed `fix:`, `feat:`, `docs:`, or `test:`.
- Do not edit generated or vendored paths: `dist/`, `target/`, `gen/`, `node_modules/`,
  `__pycache__/`, and `frontend/src/components/ui/` (shadcn-generated).
- Reuse before adding. There are already 27 UI primitives, 24 hooks, and 24 backend util
  modules; a duplicate component is the most common review finding. Extract shared code on the
  **third** occurrence, not the second.
- A change is not done until lint, formatting, and tests pass locally for every area touched.

---

## 22. Current state of this machine

- Python 3.12.10 installed alongside the pre-existing 3.14.6; both remain usable.
- `backend/.env` and `sync-microservice/.sync-env` virtualenvs created on 3.12 with all
  requirements installed.
- `frontend/node_modules` populated (899 packages).
- Rust debug binary built at `frontend/src-tauri/target/debug/picto_py.exe`.
- `rustfmt` added to the Rust toolchain (`rustup component add rustfmt`), so the
  `cargo fmt -- --check` CI gate can now run. This is the one toolchain change made beyond
  installing the project's own dependencies.
- Backend, sync-microservice, Vite, and the desktop app all verified running together, and a
  folder added through the UI was picked up by the sync watcher.
- Backend and frontend test suites pass in full (1109 and 377 tests); ESLint, `cargo fmt`, and
  markdownlint are clean.
- `npm run format:check` still reports 260 files. This is the pre-existing CRLF issue in
  §18.12, not a consequence of this setup, and it was deliberately left unfixed because the
  real fix is a tracked project file that should change via a PR.
- Chocolatey remains in its pre-existing inconsistent state. It was deliberately **not**
  repaired, because doing so affects packages unrelated to PictoPy. Nothing in the PictoPy
  development workflow needs it.
- The repository working tree is clean; no project file was modified.
