# Strict development checks on 25 November 2026

The unmodified `tools/dev/check.py` discovered 5,729 tests at `8ff69b2d412a56e740479368c64fd2361743bb28`. On native Bazzite with Python 3.13.18, 5,704 passed, zero failed and 25 were skipped. The command exited 1 because every skip is refused by default. Test execution took 1,030 seconds. The run used the recorded stock reference data and the restored original census witness with SHA-256 `006b07a39be4cff6a3fac7555b26d40ffe834594304c231dd659b23bfdc85349`. The previous twelve census errors did not recur. Full log SHA-256 was `091b1e726cbd611aeb34350c9d9a704350d9e8d1cff94cacfd3b226693835f4c`.

| Outstanding checks | Count | Required input |
| --- | ---: | --- |
| UDP owner lookup, named-pipe launcher, Steam helper, Zig cross-build, MSI tables | 13 | Native Windows. MSI table tests use a Python installation with `msilib`, such as 3.12. The cross-build test uses Zig. |
| Native UI and player settings checks | 11 | The matching x4-re symbol table and FFI references alongside the pinned executable. |
| Released lane profiles | 1 | Original released lane tool. |

## Windows follow-up

From the repository root, use the established Windows test environment at the same revision. This command selects the thirteen Windows cases listed by the Linux runner. Preserve the output including skips and the interpreter version. Run the gameplay sender check separately.

```powershell
Push-Location tests
try {
    py -3.12 -m unittest -v `
        test_authority_windows_motion_peer.WindowsMotionPeerTests.test_native_implicitly_bound_sender_is_accepted `
        test_launcher_dropin.Picker `
        test_launcher_steamplay.Helper `
        test_nat_traversal.Unit.test_linux_build `
        test_playtest_msi.Installer
} finally {
    Pop-Location
}
```

## Native Windows result

Cobalt ran the thirteen selected cases at `3ad06b66` on Windows 11 Home
build 26200 with 64-bit CPython 3.11.9. The product code, tools and
tests matched the Linux run's revision. Nine passed, including the
real UDP sender ownership lookup and three MSI checks. Three launcher
cases skipped for missing Lupa and the Linux cross-build skipped for its
toolchain. Zero failed or errored. Runtime was 11.907 seconds.

Across these two platform reports, sixteen cases still lack a passing
result, comprising those four and the twelve Linux reference checks. The final
exit of the strict Linux command remains 1. [Cobalt's native Windows results](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795590203447069)

## Linux follow-up at `11f10348`

The official runner discovered 5,777 tests at `11f10348d9fdb438ff40647a719ce6304c88cb70`. On native Bazzite with Python 3.13.18, 5,751 passed, zero failed assertions, one errored and 25 were skipped. All 25 skip IDs and reasons matched the earlier Linux run. The command exited 1 after 1,176.051 seconds of test execution. It used the same verified reference inputs and census witness, fresh Zig caches, CPUs 8–15 and nice priority +10. Full log SHA-256 was `54102b1de273a133baedbdf1bbfc0ae0f0e4fe593d22c383028599674da46129`.

The new error occurred in `test_authority_source_motion_tcp_worker.MotionWorker.test_near_then_ready_applies_only_second_sample`. During participant admission, the Linux UDP ownership guard rejected the connection with `RuntimeError: participant peer not owned by X4: connected IPv4 flow count differs from one (matches=2)`. The runner retained the traceback. This test passed in the earlier full run and in a separate isolated run at `11f10348` with the same prerequisite environment, interpreter, CPU affinity and nice priority. The isolated run took 1.222 seconds and its log SHA-256 was `7728d73f1e51b83e4cfab99487c372aec1c25d6f64ffe572017c8180790e0ce1`. The cause of the count of two remains undetermined.

All 17 participant TCP tests and all four long-source diagnostic tests passed in the official run.

At `6c6cc852` ambiguous matches also report the number of distinct socket
inodes. Two focused fixtures distinguish two socket rows for the same inode
from two different inodes, and both still fail the ownership check. All ten
UDP ownership tests passed. The original failure predates this diagnostic.
