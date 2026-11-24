# X4 Online: authoritative-world experiment

This development branch explores an alternative architecture for X4: one X4
instance owns a shared, persistent world; a designated client may simulate an
overlapping high-attention area and report results back. The first milestone is
ship positions. When a player joins, the intended flow pauses the world, saves,
transfers the checkpoint and resumes after the player has loaded it. The world
also stops and saves when the last player leaves. All players initially share
one player faction and one company account; separate factions and accounts are
deferred.

**Experimental status:** the new components are prototypes and are not a
playable authoritative multiplayer implementation. An isolated Linux test has
exercised a marked server save, recovery, one local client join and a later
save; it is not a gameplay or cross-platform acceptance test. Position
replication, client-side high-attention authority and Windows/Linux sessions
remain unproven. The original lockstep work below remains the historical base
of this branch. See
[the investigation](docs/AUTHORITY-INVESTIGATION.md) for evidence and limits.

## Original lockstep project

Every player runs the whole X4 universe from the same starting snapshot. Only
player inputs travel over the network. An opt-in native gate makes the
simulation deterministic (fixed tick, keyed random streams, fixed engine
ordering), and a relay compares a SHA-256 digest of every client's state each
tick, so any divergence is caught on the tick it happens.

Free for noncommercial use under the [PolyForm Noncommercial License 1.0.0](LICENSE.md);
see [licensing](LICENSING.md) and [notices](NOTICE.txt). This is source-available
software with a noncommercial restriction, not an OSI open-source project.

## Status

- **Works on one PC:** 2 to 4 clients have held 13 to 20 minutes with zero differing
  objects across about 543,000 compared objects ([census](reports/LOCKSTEP-CENSUS-20261028.txt)).
  Both players fly their own ships and see each other. The 2-hour soak profile
  (`src/lockstep/profiles/soak.lua`) has held up to 55.7 game minutes (100,200 ticks, seed
  2222222). On three seeds the remaining splits come from the agents' pathing
  ([findings](docs/LAUNCHER.md)). Two player identities with their own factions and
  relayed orders held 20 minutes.
- **Player actions:** a guard counts, blocks or relays every game write the UI makes,
  so one client's click happens on every client at the same tick
  ([players](docs/PLAYERS.md)).
- **Joining a running game:** a third client loads the session's save at tick T and
  replays to the session. In native tests on one PC with the join defaults it stays
  census-equal to the end of each test: 58 s after a join made before the pilot window
  starts ([hot join](reports/LOCKSTEP-HOTJOIN-20261101.txt)), and 25 s after a join
  inside the pilot window, which still needs the opt-in `native_gate_join_in_window`
  ([in window](reports/LOCKSTEP-JOIN-IN-WINDOW-20261104.txt)). Not yet tested across PCs.
- **Not yet done:** a session between two PCs (especially Intel vs AMD, whose
  approximate `rcpss`/`rsqrtss` results differ), desync recovery, and a packaged
  release. Windows vs Linux still differs in 35 objects
  at tick 600, so v1 is Windows-only.
- **Pinned build:** X4 9.00 Windows (Steam build 23660954). The gate verifies the
  exact `X4.exe` and refuses anything else. It patches only process memory at
  runtime, never files on disk ([engine instrumentation](docs/ENGINE-INSTRUMENTATION.txt)).

The earlier "replica" design (host-authoritative NPC copies, packages 7.0 to 7.5)
was retired in November 2026. It is preserved at the Git tag `legacy/replica-7.5`.

## Windows authority client

In the extracted authority client package, run
`.\tools\run_authority_windows_client.ps1 -ConnectionFile 'C:\X4Test\connection.json'`
from PowerShell. The launcher searches Steam's configured libraries for X4.
Pass `-Game` to select a specific installation.
See the [Windows authority client guide](docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md)
for the pinned build, required profile state, and optional settings.

## Quick start (developers, Windows)

```
python tools/dev/bootstrap.py                    # .venv with the LuaJIT test runtime
.venv\Scripts\python tools/dev/check.py          # Python/Lua suite (full suite targets Linux/WSL)
python tools/dev/build_native_gate.py --zig <zig 0.15.2> --test --native-image <X4.exe>
python tools/x4ls.py doctor                      # installation, pinned exe, Steam
python tools/x4ls.py snapshot --out .local/snapshots/start.xml.gz
python tools/x4ls.py session --clients 2 --overlay <built overlay> --snapshot <save>
```

`x4ls` starts every client windowed, each from its own game view and its own
disposable profile, so your normal saves and settings are never touched. See
the [launcher guide](docs/LAUNCHER.md) for overlays, Steam, and cleanup.

Linux developers can run isolated simulation comparisons with the
[local Linux validation workflow](docs/LINUX-VALIDATION.md). This is a separate
test harness; the Windows player and UI feature set has not been ported by that workflow.

## Layout

| Path | What |
|---|---|
| `src/authority/` | Experimental world coordination, checkpoints and motion/identity prototypes |
| `tools/dev/authority_*` | Internal authority experiments and isolated Linux diagnostics; see `AUTHORITY-RECOVERED-LOOPBACK-SERVER.md` for the current recovery probe |
| `src/native/engine/` | The native gate (`lockstep_gate.c` plus headers), its C tests, and the Linux gate port in `linux/` |
| `src/native/portable_math/` | Bit-exact math shared by the Windows and Linux gates |
| `src/lockstep/launcher/` | `x4ls`: sessions, snapshots, game views, profiles |
| `src/lockstep/base/` | What every client extension carries: SHA-256 helper, player-faction data, transport DLL |
| `src/rust/x4mp-agent/` | Linux fixed-tick agent used by the Linux gate work |
| `tools/dev/native_gate_*` | In-game probe (Lua), MD fixtures, overlay builders, relay, desync watcher |
| `tools/dev/census_*`, `compare_native_world*` | Desync analysis of session logs |
| `tools/build_soak_overlay.py`, `tools/soak_campaign.py` | Soak overlays (profile, players, UI guard) and unattended multi-seed soaks |
| `tools/dev/checkpoint_diff.py`, `save_diff.py`, `ui_export_census.py`, `probe_globals_check.py` | Checkpoint diffs (world part), element-by-element save diffs (join), the UI write census, the probe's static globals check |
| `tests/` | Python/LuaJIT tests (the in-game Lua runs under LuaJIT with a mocked engine) |
| `docs/` | Architecture, launcher, wire protocol, sandbox, scenarios |
| `reports/` | Lockstep evidence: census, piloting, UI replay, cross-platform |

## Architecture in one paragraph

The gate DLL is loaded by the probe Lua inside X4. Before each simulation frame
it asks the relay for a permit. The relay releases tick *t* only when every
client has reported tick *t − 1* with the same state digest. Player inputs
sampled at tick *t* are applied at *t + 3* on every client ("pilot by wire"), and
menu actions such as autopilot, docking and orders are replayed as inputs ("UI
replay"). More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
