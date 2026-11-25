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

## Integrated market, fleet and undock recovery at `494b470b`

The official Bazzite run at `494b470b4f5170d4cc5d121ce2ee654819722817`
discovered 5,809 tests under Python 3.13.18. It passed 5,783, recorded
one error and skipped 25 in 1,042.482 seconds. The exit code was 1.
The skip IDs and reasons exactly matched `11f10348`. The run began after the
native fleet provider and pilot had stopped, used the same verified inputs
and census witness, and ran with fresh Zig caches on CPUs 8–15 at nice +10.

The error was
`test_authority_client_to_server_apply_worker.Worker.test_operation_failure_survives_cleanup_failure`.
Before the test reached its simulated operation and cleanup failures, the
UDP ownership check rejected the participant handshake with
`connected IPv4 flow count differs from one (matches=2, distinct_inodes=1)`.
The sample contained two matching rows for a single socket inode. The
reason for the duplicate rows is undetermined. The earlier saved-motion
worker error did not recur. The fleet source and negotiation tests and the
strict legacy-undock recovery regression passed. The suite was run once.

The private run directory keeps the full log, exact skip list and traceback.
Full log SHA-256 was
`08119a7b596057e6cf563d3c6e0285c30b11339c8c8f8ecee07cc42d0e88d2ab`.

## Four remaining Windows checks at `1ad21866`

Cobalt ran the four outstanding dependency checks once on a clean Windows 11
Home build 26200 checkout with 64-bit CPython 3.11.9, Lupa 2.8 and Zig
0.15.2. All four passed in 25.118 seconds. Three exercised the launcher's
drop-in and Steam pages. The fourth produced the x86_64-linux-gnu.2.17
cross-build. Together with the earlier nine native Windows passes, all
thirteen Windows-specific cases now have a passing platform result across
the two recorded revisions. Twelve Linux checks still lack their original
reference inputs. The official Linux run at `494b470b` also errored once
during UDP ownership verification. The Windows results do not change its
exit code. [Cobalt's results in the development thread](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795622440162299)

Cobalt separately exercised the Windows client test modules at `bcf300cc`
without launching X4. The contribution replaces an empty Winsock
`select` sleep and handles a normal Windows disconnect in the test server.
The fork also exercises its negative IPv4 checks on real connected
non-IPv4 sockets. The ordinary file-replacement case runs even when
Windows denies symlink creation. The Company motion Lua check uses
CLI LuaJIT or Lupa's LuaJIT, and the combat checks use the pinned Lupa
runtime. The adjusted IPv6 and Lupa paths were exercised on Linux.
Cobalt's Windows measurements apply to his submitted revision.

A local reference inventory also checked the available project archives for
the original UI/FFI inputs and the released lane tool. The historic lane
directory was named `release-5d5a680ec6ef`. A development copy on
`contrib/ferrite-rw-lane` contains four matching profile strings, but has
no original release archive or release manifest for comparison. The named
commit was unavailable from either checked upstream repository. The twelve
reference-dependent cases remain open.

## Registered NPC alignment and boarding witness at `1f6fb7b6`

One official Bazzite run at `1f6fb7b6c441e54dcbf931e2fee8a63e76d068b6`
discovered 5,847 tests on Python 3.13.18. All 5,822 executed tests
passed, including both worker cases that errored in earlier full runs.
The 25 skipped test IDs and reasons matched `494b470b` exactly. The
default strict runner exited 1 on those skips. Thirteen have separate
Windows results. The other twelve still need their original references.

The run used the same 19 verified prerequisites, fresh Zig caches,
CPUs 8–15 and nice priority +10. The suite took 1,036.873 seconds.
Full log SHA-256 was
`b6c93f52dcf7ff1dad424c4bf6560c656c2d053997c2bd768729d95d6f6e7f4c`.

## Operator build at `04f409f6`

One official Bazzite run of `tools/dev/check.py` at
`04f409f68eae3078666191a6c0c4221ac7415ec6` discovered 5,878 tests. Of the
5,853 executed, 5,852 passed and one errored, with zero assertion failures.
The 25 skip IDs and reasons matched `1f6fb7b6` byte for byte. Thirteen have
separate Windows results and twelve still need original references. The
runner exited 1. It used CPython 3.13.18, the same 19 verified references, fresh
Zig caches, CPUs 8–15 and nice priority +10. Test execution took 1,036.644
seconds. Full log SHA-256 was
`06075e54f309753ba90e427871505dd76238416408b48785a2d93da3c1ee2ec3`.

`test_fixed_native_quote_and_order_share_the_post_duplicate_lookup` errored
with `StopIteration` while looking for the pre-sale XML condition that matched
only Buy. The follow-up test on `9c558603` verifies that Buy and Sell set the
submission flag, Quote cannot book an order, and Sell requires the fixed
live-sale path. It checks that duplicate checks precede the directional
offer lookup and that the fixed quote and guarded order both use the
newly read offer and price. The six tests in
`test_authority_company_trade_trial.py` passed in one targeted run, including
its LuaJIT checks. The full-suite result above remains the recorded result
for the packaged revision.
