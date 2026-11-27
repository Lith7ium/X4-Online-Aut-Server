# X4 Online — a shared universe for co-op

This independent fan project is developing multiplayer for X4: Foundations. A Linux server runs a licensed copy of X4 and owns the universe and its save. The goal is for up to four pilots to fly separate ships, travel to different sectors and share one Company, fleet and account. The world pauses and saves when the last pilot leaves.

**27 November 2026 · weekend release on hold.** Two people joined the same Fedora-hosted world from Linux and Windows over the internet. They saw each other's ships move at close range. The flight was jerky, the heading was wrong, and the observing pilot lost movement when the other ship entered travel drive. The server recorded damage in both directions, but the pilots could not confirm incoming damage in their cockpits. [Human playtest](reports/AUTHORITY-HUMAN-PLAYTEST-20261126.md)

Since that flight, two automated Linux clients have followed a 4.98 km travel run and a 29.6° turn. In a separate Fedora-hosted flight, the selected pilot's map arrow followed 2.05 km of real movement. Braking accuracy, the final saved position on departure while moving, and sustained gunfire still have open issues. These changes await another human Linux/Windows internet flight. [Travel measurements](reports/AUTHORITY-HUMAN-PLAYTEST-20261126.md#travel-drive-and-braking-with-two-v4-clients) · [Shared map flight](reports/AUTHORITY-HUMAN-PLAYTEST-20261126.md#selected-map-ship-in-a-shared-linux-session)

## What works today

The [gameplay status](docs/AUTHORITATIVE-STATUS.md) covers joining, persistence, flight, combat, trade, fleet orders and boarding. Each entry identifies who or what actually played. Readers looking for a co-op release should start there.

## Reading the code

| Follow one operation | Start here |
| --- | --- |
| Join a world, reserve a ship and recover a save | [Admission](src/authority/trusted_marker_load_session.py) and [checkpoints](src/authority/checkpoints.py) |
| Move a ship and choose which other ships to observe | [Movement service](src/authority/company_motion_service.py) and [client](src/authority/company_motion_client.py) |
| Follow a hit from the gun to the target cockpit | [Pilot sender](src/authority/company_combat_source.py), [server ordering](src/authority/company_combat_service.py), [X4 damage call](tools/dev/authority_company_gun_damage.xml) and [cockpit readback](src/authority/company_cockpit_health.py) |
| Spend shared credits or command another Company ship | [Trade ledger](src/authority/company_purchase_ledger.py) and [fleet dispatch](src/authority/company_fleet_dispatch.py) |
| Start the Linux server or a pilot | [Provider entry point](tools/dev/authority_provider_server.py), [Linux guide](docs/AUTHORITY-SERVER-PACKAGE.md) and [Windows guide](docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md) |

Python owns the session and network protocols. Lua reads and updates the game UI. Mission Director XML resolves registered ships and asks X4 to perform actions. Tests under [tests](tests) exercise those boundaries. Dated [reports](reports) contain native-game observations and setup details.

## Developer checks

On Windows, use the [fresh-checkout steps](docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md#start-from-a-fresh-clone)
to build the current UDP module and run a local check before arranging a game test.

Install Python 3 with virtual environment support, a C compiler (`cc`) and the standalone `luajit` command. From the repository root on Linux, run

```sh
python3 tools/dev/bootstrap.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_authority_company_combat_service.py
.venv/bin/python tools/dev/check.py
```

The middle command exercises hit ordering, deduplication and health reads with a local test adapter. It needs no game installation. The final command runs the repository suite without starting X4. Some checks require locally built native tools, licensed game files or evidence from an earlier native run. The runner reports skipped checks and exits unsuccessfully for unapproved skips. The [CI workflow](.github/workflows/ci.yml) records its build prerequisites.

## Related work and license

Two approaches are in active development. This fork uses a central X4 simulation. Silver's lockstep implementation runs the simulation on each participant and synchronizes their inputs. Both grew out of the earlier multiplayer work. [Silver's project](https://github.com/Unofficially-Online/X4-Online) · [Lockstep architecture](docs/ARCHITECTURE.md) · [Early authority experiments](docs/AUTHORITY-EARLY-EXPERIMENTS.md)

Original project code is available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md). See [licensing](LICENSING.md) and [third-party notices](NOTICE.txt). Players and server operators supply their own X4 installations.
