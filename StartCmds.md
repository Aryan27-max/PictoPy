# StartCmds — how to restart PictoPy locally

Quick reference for bringing the development environment back up after you have closed it.

Everything here was verified working on this machine. For the reasoning behind it, see
`Understanding.md`.

---

## The short version

Three terminals, **in this order**, all from `C:\Users\gupta\PictoPy`:

```powershell
# Terminal 1 — backend (MUST be first)
cd C:\Users\gupta\PictoPy\backend
.\.env\Scripts\Activate.ps1
fastapi dev --port 52123
```

```powershell
# Terminal 2 — sync microservice (after the backend is up)
cd C:\Users\gupta\PictoPy\sync-microservice
.\.sync-env\Scripts\Activate.ps1
fastapi dev --port 52124
```

```powershell
# Terminal 3 — desktop app
cd C:\Users\gupta\PictoPy\frontend
npm run tauri dev
```

Leave all three running. Terminal 3 opens the PictoPy window.

---

## Why the order matters

This is not a style preference — it will actually fail if you get it wrong.

The sync microservice checks for a `folders` table in the shared SQLite database on startup.
Only the backend creates that table. If you start sync first on a machine where the database
does not exist yet, it retries every 5 seconds for 60 seconds and then **exits** with
"Database connection failed after multiple attempts".

Start the backend, wait for it to report it is running, then start sync.

The desktop app can be started at any point after that, but nothing will load in the UI until
the backend is up — `npm run tauri dev` does **not** start the Python services for you.

---

## Alternative: no virtualenv activation

If `Activate.ps1` is blocked by PowerShell's execution policy, or you just prefer fewer steps,
call the interpreter in the virtualenv directly. This is equivalent and slightly more reliable:

```powershell
# Terminal 1 — backend
cd C:\Users\gupta\PictoPy\backend
.\.env\Scripts\fastapi.exe dev --port 52123
```

```powershell
# Terminal 2 — sync microservice
cd C:\Users\gupta\PictoPy\sync-microservice
.\.sync-env\Scripts\fastapi.exe dev --port 52124
```

```powershell
# Terminal 3 — desktop app
cd C:\Users\gupta\PictoPy\frontend
npm run tauri dev
```

**`fastapi dev` still needs the right working directory.** With no path argument it looks for
`main.py` in the current directory, so running it from somewhere else fails with:

```text
ERROR    Could not find a default file to run, please provide an explicit path
```

If you want commands that work from *any* directory — handy for a shortcut or a script — pass
the full path to `main.py`. FastAPI resolves the package root from the file, so imports still
work:

```powershell
C:\Users\gupta\PictoPy\backend\.env\Scripts\fastapi.exe dev C:\Users\gupta\PictoPy\backend\main.py --port 52123
```

```powershell
C:\Users\gupta\PictoPy\sync-microservice\.sync-env\Scripts\fastapi.exe dev C:\Users\gupta\PictoPy\sync-microservice\main.py --port 52124
```

---

## Check that it actually came up

```powershell
curl.exe http://localhost:52123/health
curl.exe http://localhost:52124/health
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:1420/
```

Expected:

```text
{"message":"PictoPy Server is up and running!"}
{"status":"healthy","database":"connected","watcher":"running"}
200
```

`"watcher":"stopped"` is also fine — it just means no folders are registered yet. The watcher
starts once you add a folder in the app.

To see everything at once:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 1420,52123,52124 |
  Select-Object LocalPort, OwningProcess
Get-Process picto_py -ErrorAction SilentlyContinue |
  Select-Object Id, MainWindowTitle
```

---

## Ports in use

| Port | What |
| --- | --- |
| 52123 | Backend API |
| 52124 | Sync microservice |
| 1420 | Vite dev server (Tauri loads this) |
| 52125 | Album share listener — only while a share is active |

Port 1420 is **strict**: if something else holds it, `npm run tauri dev` fails rather than
picking another port.

---

## Stopping everything

### Closing the window is not quitting

By default PictoPy **hides to the system tray** when you click the window's X. The process keeps
running. To actually exit, right-click the PictoPy tray icon and choose **Quit** — that path
also shuts the Python services down cleanly.

You can change this in the app's settings ("close to tray").

### Stopping the terminals

Press `Ctrl+C` in each of the three terminals. Stop the desktop app first, then sync, then the
backend.

### If something is left behind

```powershell
# See what is still holding the dev ports
Get-NetTCPConnection -State Listen -LocalPort 1420,52123,52124,52125 |
  Select-Object LocalPort, OwningProcess

# Ask the services to shut down cleanly (preferred)
Invoke-WebRequest -Method Post -Uri http://localhost:52123/shutdown -UseBasicParsing
Invoke-WebRequest -Method Post -Uri http://localhost:52124/shutdown -UseBasicParsing

# Last resort
Stop-Process -Name picto_py -Force -ErrorAction SilentlyContinue
```

Both services expose a `/shutdown` endpoint; that is how the Rust shell stops them on Windows,
so prefer it over killing processes.

---

## Troubleshooting

### "Port 1420 is already in use"

A previous `tauri dev` did not exit. Find and stop the owner:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 1420 | Select-Object OwningProcess
Stop-Process -Id <that id> -Force
```

### Sync exits after ~60 seconds

You started it before the backend. Start the backend first, confirm
`http://localhost:52123/health` responds, then start sync again.

### `Activate.ps1 cannot be loaded because running scripts is disabled`

Either use the direct `.exe` form above, or allow scripts for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### `fastapi` is not recognised

The virtualenv is not active. Either activate it, or call the `.exe` inside the virtualenv
directly.

### `Could not find a default file to run, please provide an explicit path`

You are in the wrong folder. `fastapi dev` needs `main.py` in the current directory — `cd` into
`backend` or `sync-microservice` first, or pass the full path to `main.py` as shown above.

### The virtualenvs are missing entirely

Recreate them. Note the interpreter is pinned to 3.12 on purpose — the pinned `numpy`, `scipy`,
`opencv-python`, and `onnxruntime` versions have no wheels for newer Python:

```powershell
cd C:\Users\gupta\PictoPy\backend
py -V:3.12 -m venv .env
.\.env\Scripts\python.exe -m pip install -r requirements.txt

cd C:\Users\gupta\PictoPy\sync-microservice
py -V:3.12 -m venv .sync-env
.\.sync-env\Scripts\python.exe -m pip install -r requirements.txt
```

### The UI loads but every request fails

The backend is not running, or it is on the wrong port. The frontend has
`http://localhost:52123` hard-coded, and the Tauri CSP only permits 52123 and 52124.

### First `npm run tauri dev` after a `cargo build` recompiles a lot

Expected. The Tauri CLI builds with its own environment, so it does not fully reuse a plain
`cargo build`. Later runs are fast.

### The UI is blank or stale after a code change

Vite hot-reloads `frontend/src` automatically. Rust changes in `frontend/src-tauri` require
`tauri dev` to rebuild, which it does on save — give it time.

---

## A note on `git status`

Starting the backend rewrites a tracked file, `docs/backend/backend_python/openapi.json`, every
single time. It will show as modified even though you changed nothing. Do not commit it unless
updating the API spec is the point of your change:

```powershell
git checkout -- docs/backend/backend_python/openapi.json
```

---

## Useful extras

```powershell
# Backend API docs (while the backend is running)
start http://localhost:52123/docs

# Run the test suites
cd C:\Users\gupta\PictoPy\backend
$env:GITHUB_ACTIONS = "true"     # keeps tests off your real photo database
.\.env\Scripts\python.exe -m pytest

cd C:\Users\gupta\PictoPy\frontend
npm test
```

Setting `GITHUB_ACTIONS=true` matters: without it, `pytest` runs against your real library
database at `%LOCALAPPDATA%\PictoPy\PictoPy\database\PictoPy.db`.
