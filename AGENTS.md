# Public source maintenance

## 0.1.31 update and internal Skill delivery

- Billing dashboard is the user-approved `https://apikey.fun/dashboard`; open it in the system browser without passing API keys or reading browser credentials.
- Updates use the fixed GitHub repository and an Ed25519 signed component manifest. The private signing key must never enter source, logs, public packages or releases. A checksum is not a publisher signature; neither is a Windows Authenticode certificate.
- Keep active queues and ongoing HTTP work out of installation. Failed parent tasks with pending historical segments must not permanently block updates. Preserve old version directories and back up SQLite before switching.
- Internal business Skill packages are private. Import atomically, keep user edits/disabled records, and record package IDs. Corrupt optional packages must report failure without preventing an existing app from starting.
- Focused Python tests: `services/api/tests/test_software_updates.py`, `test_skill_bundle.py`, `test_installer_delivery.py`, `test_s3_skills.py`. Browser test: `scripts/verify-s5-dialog.ps1 -RunRoot <fresh E directory> -BrowserScript verify-update-billing.cjs -PythonExecutable <python> -AdditionalPythonPath <optional crypto runtime>`.
- Repeat the core checks and test the exact final installer. Do not call mocked network/UI states real upgrade acceptance. Current internal Skill snapshots do not include original videos or the hidden S5 full report chain.

- Preserve user data, existing installations and unrelated changes. Do not publish API credentials, private configuration packages, videos or business databases.
- On the maintainer workstation, downloads/caches/builds go under `E:\Codex工作盘`, with explicit absolute output paths. Never use the production data directory for tests.
- Product workspaces are material analysis, independent editing projects, and approved finished media batches. Keep evidence references and manual correction results intact.
- Development: `scripts/dev-tauri.ps1`. Installed entry: bundled Python invokes `scripts/installed_launcher.py`; never require a colleague to install development tools.
- Core regression: `scripts/verify-release-python.ps1 -PythonExecutable <python> -RunRoot <fresh isolated directory>`, plus desktop Vitest and TypeScript checks. Generate synthetic media fixtures when running media integration tests.
- Installer regression includes `services/api/tests/test_installer_delivery.py`. Also execute the real packaged service and native window. A successful compile is not deployment acceptance.
- Report actual tests separately from untested colleague machines, actual model requests separately from mocked providers, and checksum verification separately from code signing.
- Public releases must exclude internal configuration and private data. Do not delete existing releases or rewrite Git history.
