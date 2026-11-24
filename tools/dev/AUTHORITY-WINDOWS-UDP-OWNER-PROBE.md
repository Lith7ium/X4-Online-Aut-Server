# Local Windows UDP owner check

Use Windows x64 Python 3.11 or later. The test opens one local IPv4 UDP
sender and receiver in the diagnostic process. It does not start X4,
connect to a provider, open a game profile or inspect another process.
Compare the ZIP SHA-256 with the digest supplied by the operator first.

Extract the ZIP into a fresh directory. In PowerShell, give the diagnostic
the path to the existing Windows X4 installation's `lua51_64.dll`. For
example, with a Steam library on D:

```powershell
python .\tools\dev\authority_windows_udp_owner_probe.py --lua-dll 'D:\SteamLibrary\steamapps\common\X4 Foundations\lua51_64.dll'
```

Use `py` if that is how Python is invoked on your system. The diagnostic
checks the installed Lua DLL against the pinned build and reads it in
place. It does not distribute or change it. The program loads the supplied
project network DLL through `luaopen_x4lan_net` in that Lua state. The DLL
opens the same nonblocking, implicitly bound Winsock UDP socket it uses
in the game and sends one synthetic datagram to the local receiver. The
same DLL's monotonic clock is checked before the socket closes.

If that Lua host is unavailable, run the explicitly labelled model:

```powershell
python .\tools\dev\authority_windows_udp_owner_probe.py --model
```

The model uses Python to make the same IPv4 Winsock calls and reads QPC
through Windows. It verifies the packaged network DLL hash but does not
execute the DLL or verify the Lua ABI. If both modes are available, their
outputs can be compared as separate observations.

Share just the single JSON line. It contains no paths, addresses, ports,
process IDs or credentials. The report samples the Windows IPv4 owner
table five times while the socket is open and categorizes only rows for
the diagnostic's own process and the observed source port.

- `loopback` means one matching IPv4 row marked as loopback.
- `wildcard` means one matching IPv4 row marked as all interfaces.
- `missing` means no matching IPv4 row.
- `multiple` means multiple matching IPv4 rows.
- `other` means one matching row with another local address.
- `inconsistent` means the category changed across the five samples.

A completed diagnostic can report any of those outcomes. A
`failure` appears as `status: failed` with a bounded stage label.
`lua_host_hash` failure usually means the pinned Lua DLL is absent or
differs. The underlying table query is system-wide, but the code
filters by its own PID before decoding matching addresses. The probe
does not check IPv6 port conflicts or certify the game's process owner.
Keep the shipped DLL and the project's runtime identity checks intact.
