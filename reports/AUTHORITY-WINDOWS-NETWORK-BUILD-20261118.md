# Windows fixture network module build

The original step recorded below was a cross-build and static inspection.
A subsequent isolated Wine execution is recorded separately at the end. Neither
step is a native Windows or X4 participant test.

## Why a separate build is needed

`src/authority/lua/local_udp_module.lua` requires the loaded native module to
provide both `open` and `monotonic`. The historical tracked DLL at
`src/lockstep/base/native/x4lan_net_64.dll` last changed at `6de08eed`; the source
at that revision registers only `open` on the module table. Static string
inspection of that DLL also found no `monotonic` registration name or
performance-counter import. Its SHA-256 is
`0fc98dba4d6844ffe2f59e0d827f73e5641b1c6aa5d71071641cabb4f80b6ab6`.
It is not suitable for the new fixture client. The legacy binary was left
unchanged; the new client accepts an explicitly supplied build and digest.

## Recorded build

Built at repository revision `d8bfeda3` using the existing `src/native/build.py`,
Zig 0.15.2, `--platform windows`, and a new output directory:
`.local/windows-fixture-network-20261118-01/`.

| Input/artifact | SHA-256 |
| --- | --- |
| `src/native/net.c` | `143185e4e0ef5e32aca4b000814b420ea71c374faa6dc3a193dcc3ca2e813191` |
| `src/native/build.py` | `a1b65d0484a1481bce09809346cd91d7cfa021f2d54c94988378eb0bb4c66059` |
| Built `x4lan_net_64.dll` | `6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0` |

The build completed successfully with `-Wall -Wextra -Werror`. `file` identified
a PE32+ x86-64 DLL; `objdump -p` found the `luaopen_x4lan_net` export and imports
of `QueryPerformanceCounter` and `QueryPerformanceFrequency`. The built file
contains the `monotonic` and `pending` registration names. These checks support
the intended binary target and API implementation, not successful DLL loading
or execution. The hash identifies this retained local artifact, not a promise
that every build environment produces identical bytes.

## Later native Windows evidence

Neon verified that an isolated Windows 11 X4 9.00 process loaded the
packaged November 18 DLL, then observed 37 motion samples acknowledged by a
local loopback service. [Native Windows sender report](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/8e2880cab4632afe20e09a4655dae4fcf63e591f/reports/AUTHORITY-WINDOWS-SENDER-DLL-20261125.md)

Cobalt reported that a separate isolated Windows X4 9.00 cockpit run
loaded the current module and measured 0.019 s absolute skew between
X4's monotonic clock and the Python receiver. The run used a local fixture
for the movement window and health replies. WAN transport and authenticated
server admission were outside this test. [Native cockpit fixture](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/1bb2eace5c574f827700624ddf023773e95b58a8)

## Real DLL execution under Wine

At checkout `665fde6b` plus the new
`tools/dev/authority_windows_network_smoke.py`, one Windows Python process under
GE-Proton10-29's Wine loaded the existing Windows depot's `lua51_64.dll` read-only
and created a Lua state. The runtime reported `Lua 5.1;LuaJIT 2.1.0-beta3`.
Its SHA-256 is
`e0712ac93f0d7f3a28236d3153b82e85d35e6bcf8e606af250f664580eaf3b63`.
Lua's real `package.loadlib` then loaded the network DLL identified in the table
above. No game process, Steam, profile or save was involved; Egosoft files were
not modified or copied into the repository.

The process exited with code zero and all ten reported checks passed:
DLL loading, initially empty socket, oversized send rejection, loopback UDP send,
pending/receive behavior for empty, oversized and ordinary datagrams, advancing
monotonic time, repeat close and post-close behavior, and Lua state cleanup.
The clock advanced by 0.0206616 seconds across the timed portion. This exercises
the actual Windows machine code and its Lua ABI under Wine, not a mocked native
module. Both the runtime and network DLL hashes were unchanged afterward.

The outer harness nevertheless recorded **overall failure**: its final scan
could not read every same-user process environment and therefore could not
confirm its full cleanup condition. The launcher was reaped, the prefix-specific
wineserver stop/wait commands succeeded, and no matching prefix process was
found. A later read-only snapshot found no readable Wine executable candidate;
the unreadable environments belonged to processes named systemd, sd-pam,
kwin_wayland, polkit-kde-auth and ssh-agent. This later snapshot does not turn
the original outer failure into a passing complete-process-tree result. The
DLL test was not repeated to change that classification.

Private evidence: `.local/windows-network-runtime-wine-20261118-01/`, including
`wine.log`, `client-result.json`, `result.json`, `command.json` and
`cleanup-followup.json`; the wrapper is
`.local/windows_network_runtime_wine_20261118.py`. The portable probe and its
[usage guide](../tools/dev/AUTHORITY-WINDOWS-NETWORK-SMOKE.md) are tracked. The
probe contains no shipped DLL or Egosoft asset. Its Wine detection and result
fields explicitly separate Wine from native Windows. Native Windows execution,
X4's UI loader, game callbacks, Internet transport and shared-world gameplay
remain untested by this observation. No full test suite was run.
