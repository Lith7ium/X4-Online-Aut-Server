# X4 Online — a shared universe for co-op

This independent fan project is developing multiplayer for X4: Foundations. A Linux server runs a licensed copy of X4 and owns the universe and its save. The goal is for up to four pilots to fly separate ships, travel to different sectors and share one Company, fleet and account. The world pauses and saves when the last pilot leaves.

**Development update · 26 November 2026.** Two people met in Black Hole Sun V. A Fedora laptop hosted the base-game universe, one pilot joined from Linux on the LAN and the other from Windows over the internet. They could see each other's fighters. The Linux pilot could target the other ship, but its movement and the damage from shooting it did not appear correctly across both screens. The server saved their world when they left. [Human flight report](reports/AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md)

Since then, two automated Linux cockpits on one machine have each measured the other ship moving. A running Fedora server booked a 26-point hit once. The target pilot's automated connection read 2,340 shield points, down from 2,366, including after reconnecting. In a separate Windows test, a real cockpit applied both lower and higher health values supplied by a test fixture. These changes still need a human Linux/Windows flight and combat test over the internet. [Movement measurements](reports/AUTHORITY-TWO-PILOT-FLIGHT-20261125.md) · [Combat evidence](reports/AUTHORITY-SHIELD-REPLAY-20261126.md)

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
