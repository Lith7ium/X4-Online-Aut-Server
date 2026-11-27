# Restarting a dedicated world after a crash

Proposal, 27 November 2026. It is not implemented and needs a decision by the
fork's technical lead before any code changes. The dedicated launcher
(`tools/dev/authority_dedicated_server.py`) currently restarts from the newest
cleanly stopped run, so a crash loses everything since that stop.

## Why crashed checkpoints are refused today

`verify_previous_source` in `tools/dev/authority_recovered_loopback_server.py`
accepts a source store only together with its run's `result.json`. The run
writes that result at a clean stop, and it proves three things for the final
checkpoint:

1. **No writer remains.** The supervisor stopped every owned process and file
   service, with no cleanup errors (`owned_processes_remaining == []`).
2. **The registry is the one this server verified.** The result's
   `registry_snapshot.final_receipt` must equal the receipt computed from the
   saved registry evidence.
3. **The Company ledger is sealed.** The result's `company_purchase_ledger`
   must match the ledger in the save. No purchase may be half-settled.

A crash can leave confirmed checkpoints in the store (`CURRENT`, `CONFIRMED`).
It never writes this evidence for them. Recomputing the evidence from the save
alone would accept whatever the save contains. That is exactly what the check
is meant to prevent, so the launcher does not do it.

## Proposal: seal each checkpoint when it is confirmed

Write the clean-stop evidence for every confirmed checkpoint, not only for the
last one.

- After `WorldCoordinator.step` confirms a save, write
  `checkpoints/<checkpoint id>.seal.json` atomically: a temporary file, fsync,
  rename, then fsync of the directory. The seal holds:
  - the supervisor version and world epoch;
  - the checkpoint id and SHA-256;
  - the registry receipt;
  - the Company ledger reference.
- Each save already happens only after `purchases.before_world_transition()`
  and with no native combat write pending, so the ledger is settled at that
  moment. The seal records the same values a clean stop records.
- Computing the registry receipt needs `verify_join_snapshot` on the new save.
  This lengthens each checkpoint pause. Measure it first.
- Add a separate `--crash-source` mode. It accepts the newest checkpoint that is
  both `CONFIRMED` and has a matching seal. It keeps every other check in
  `verify_previous_source`: scripts, content, marker and fixture.
- Fence before trusting the store:
  - no X4 process of the user runs;
  - the old run's X4 lock is gone (`--clear-stale-x4-lock` already proves this);
  - under systemd the previous unit's control group is empty.
- Record `crash_source: true` and the skipped run in the new result, so reports
  can tell a crash restart from a clean one.

## Tests needed before use

- Offline: a fake engine and store, with the supervisor killed between
  `publish` and `confirm`, after `confirm` but before the seal, and after the
  seal. Only the last case may restart from the new checkpoint.
- In X4: kill the supervisor with SIGKILL during an empty keep-running run,
  after at least one empty checkpoint. Restart, then compare the registry
  count and Company ledger with the sealed values. Repeat once with a
  purchase settled shortly before the kill.

## Open questions

- Is a longer pause at each checkpoint acceptable while pilots are connected?
- Should a crash restart be automatic, or wait for the operator to confirm it?
