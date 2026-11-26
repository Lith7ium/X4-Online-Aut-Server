# Working on this repository

- This branch explores an authoritative-world alternative for X4 9.00 on top
  of the existing lockstep project. Read README.md and
  docs/AUTHORITATIVE-STATUS.md for the current implementation and evidence.
  docs/AUTHORITY-INVESTIGATION.md records the starting investigation. The
  file docs/ARCHITECTURE.md describes the original
  lockstep design. Read docs/LAUNCHER.md before running X4.
- Never load, write or delete the player's normal X4 profile, saves or settings.
  Use the launcher's disposable profiles. Never modify or redistribute Egosoft
  files; the gate patches memory only and must keep failing closed on any other
  build.
- Close X4 before native tests. Run X4 test clients windowed.
- Checks: `tools/dev/check.py` (the full suite targets Linux/WSL; skipped tests
  count as failures), and `tools/dev/build_native_gate.py --zig <0.15.2> --test
  --native-image <X4.exe>` for gate changes. A synthetic or mocked test is not
  evidence of behaviour in the real game; say which one you ran.
- Keep `.local/` for logs, overlays, snapshots, toolchains and credentials; never commit it.
- Write original code. Neresco projects are research references only.
