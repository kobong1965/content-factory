# Public source maintenance

- Preserve user data, existing installations and unrelated changes. Do not publish API credentials, private configuration packages, videos or business databases.
- On the maintainer workstation, downloads/caches/builds go under `E:\Codex工作盘`, with explicit absolute output paths. Never use the production data directory for tests.
- Product workspaces are material analysis, independent editing projects, and approved finished media batches. Keep evidence references and manual correction results intact.
- Development: `scripts/dev-tauri.ps1`. Installed entry: bundled Python invokes `scripts/installed_launcher.py`; never require a colleague to install development tools.
- Core regression: `scripts/verify-release-python.ps1 -PythonExecutable <python> -RunRoot <fresh isolated directory>`, plus desktop Vitest and TypeScript checks. Generate synthetic media fixtures when running media integration tests.
- Installer regression includes `services/api/tests/test_installer_delivery.py`. Also execute the real packaged service and native window. A successful compile is not deployment acceptance.
- Report actual tests separately from untested colleague machines, actual model requests separately from mocked providers, and checksum verification separately from code signing.
- Public releases must exclude internal configuration and private data. Do not delete existing releases or rewrite Git history.
