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
