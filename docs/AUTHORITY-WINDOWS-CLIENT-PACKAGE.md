# Internal Windows authority client diagnostic

This package runs the Windows fixture diagnostic with a matching authority test
server. A run observes the join, checkpoint, and participant control. Company
ship selection requires a matching server and client revision. The package is
for internal diagnostic trials.

## Build the ZIP

Run this from the diagnostic checkout with Git and Python 3.11 or later.
Choose a committed revision containing the desired Windows client features.

```bash
python tools/make_authority_windows_client_package.py --ref HEAD \
  --out /path/to/new/x4-authority-windows-diagnostic.zip
```

To include the project-built UDP module, add `--recorded-network-dll` pointing
to the retained `x4lan_net_64.dll` from the
[November 18 build](../reports/AUTHORITY-WINDOWS-NETWORK-BUILD-20261118.md).
The builder checks that DLL's recorded SHA-256 and the selected revision's
two corresponding source digests. Otherwise the operator supplies a verified
current-source build when running the package. The ZIP records the source
commit and the DLL provenance when bundled. Share the printed ZIP SHA-256 and
the server's private `connection.json` with the tester through a trusted
channel. The connection file is an external input. The package uses the
tester's installed X4, Steam, and Python.

## Run on Windows

Extract the ZIP and open PowerShell in `x4-authority-windows-diagnostic`.
Use Python 3.11+, running Steam, the pinned Windows X4 9.00 build, and the
server's private `connection.json`. Close X4 first. The existing Windows
workspace checks the game executable and requires the actual
`Documents\Egosoft\X4` root to exist with profile `45841` absent. Retained
artifacts from an earlier trial need to be handled separately before repeating
the diagnostic.

Compare the archive SHA-256 with the build operator's value. The optional
connection check contacts both authenticated server services with the supplied
file. It can run before opening X4.

```powershell
python .\tools\dev\authority_connection_check.py --connection-file C:\X4Test\connection.json

.\tools\run_authority_windows_client.ps1 `
  -Game 'D:\SteamLibrary\steamapps\common\X4 Foundations' `
  -ConnectionFile C:\X4Test\connection.json `
  -WorkRoot D:\X4Test
```

`-WorkRoot` is an existing writable directory on the game drive. The default
is the parent of the X4 installation. Each invocation picks fresh sibling
view and run paths there. The Python workspace creates them with private ACLs
and preserves them after the run.

Add `-ChooseCompanyShip` when the operator confirms that the packaged client
and server both support company ship selection. Use an interactive PowerShell
terminal so the Python client can read the choice. This mode holds the client
for 15 minutes after admission by default, allowing time for a second player
to join. Use `-ObserveSeconds` and `-TimeoutSeconds` to set the test duration
and the overall limit (up to one hour). Omit the flag for the
ordinary fixture join. For a source-only package, add `-NetworkDll` with the
trusted Windows UDP module path and `-NetworkDllSha256` with the hash supplied
by its trusted builder. The [fixture guide](../tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md)
covers the current-source DLL build, connection file, and retained artifacts.


For servers with company motion enabled, add `-CompanyMotion` to the same
PowerShell invocation. This opens the terminal ship selection and uses the
separate stream through the same pinned TLS endpoint once the ship is admitted.
The run result records movement counts. The temporary motion credential stays
in memory.
