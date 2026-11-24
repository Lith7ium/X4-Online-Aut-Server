# Portable format-3 recovery seed

This Linux tool packages one stopped experimental supervisor's latest confirmed
format-3 checkpoint. It preserves the checkpoint bytes, its store manifest and
both matching store pointers, the four guarded MD scripts, and the disposable
test profile's extension list. The supervisor result is reduced to the guarded
completion fields and final format-3 receipt. The archive excludes earlier
generations, unrelated profile files, logs, game binaries, and transport credentials.

A successful export requires a cleanly ended, paused source, the matching final
registry receipt, and a saved MD recovery marker. Use a stopped private source.
Paths and the expected MD bytes must match the supervisor version that produced
the seed. Treat the archive as private game-state data.

From the repository root, create an archive and restore it into a new directory.

```bash
python tools/dev/authority_portable_seed.py export \
  --source /private/stopped-supervisor --archive /private/confirmed-format3.zip
python tools/dev/authority_portable_seed.py restore \
  --archive /private/confirmed-format3.zip --out /private/restored-supervisor
```

Restore checks member names, sizes, and SHA-256 digests before validating the saved
marker and supervisor source guard. To compare the retained extension list with
a separately prepared private profile, add `--profile /private/profile` to
`restore` or run the same check after extracting.

```bash
python tools/dev/authority_portable_seed.py verify \
  --source /private/restored-supervisor --profile /private/profile
```

Use `/private/restored-supervisor/checkpoints` as the supervisor's `--store`.
This packages and verifies the recovery input. It does not start X4, run the
simulation without a GPU, or publish to a provider.
