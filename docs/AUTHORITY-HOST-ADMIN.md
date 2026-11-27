# Host admin menu

The dedicated server can serve a private web page for the server host. It is
off by default. On that page the host can:

- see connected pilots, kick a pilot, and hold or assign Company ships;
- add or remove Company money;
- repair, rename and load Company ships;
- set faction relations;
- add blueprints;
- add building resources to station build storage;
- unlock research.

The page is our own design. It takes ideas from cheat menus on the Steam
Workshop, but it contains no code or assets from any mod.

This menu has not yet run against a live X4. The MD script validates against
the game's `md.xsd`, and the Lua, Python and page have offline tests only.

## Start the server with the menu

Add two provider options. The menu needs `--require-registry-snapshot`.

```sh
python3 tools/dev/authority_provider_server.py ... --require-registry-snapshot \
  --host-admin-port 8765
```

For the systemd service, add them after `--` in `X4_LAUNCHER_OPTIONS` in
`~/.config/x4-authority/server.env`:

```sh
X4_LAUNCHER_OPTIONS=--empty-checkpoint-seconds 900 --keep-runs 10 --clear-stale-x4-lock -- --base-game-only --require-registry-snapshot --host-admin-port 8765
```

On its first start the server creates the host token in
`~/.config/x4-authority/host-admin.token` with mode 600. Use
`--host-admin-token-file PATH` for a different file. Delete the file and
restart the server to get a new token.

## Open the menu

The page listens on `127.0.0.1` only. From another computer, open an SSH tunnel
to the server, then browse to `http://localhost:8765`:

```sh
ssh -L 8765:127.0.0.1:8765 operator@server
```

Paste the token when the page asks for it. The page keeps it in this browser
tab only.

## What each section does

| Section | What it changes |
| --- | --- |
| Pilots | Kick a pilot. This closes the pilot's connection, the same as a lost connection, and the pilot can join again. Assign a ship to a pilot who is still choosing. |
| Ships kept for the host | A held ship is offered to no joining pilot. A ship assigned to the next pilot is the only ship offered to the next pilot who connects. |
| Company ships | Repair to 100% hull and shield, rename, or add cargo. Only ships in the server's Company registry are listed. |
| Money | Adds or removes credits in the player faction's account, up to one billion per action. The balance never goes below zero. |
| Faction relations | Sets your faction's relation to another faction, in both directions, on the game's −30 to +30 scale. Locked relations are refused. |
| Research | Unlocks research at once, one topic or all of them. |
| Blueprints | Adds station module, ship, equipment or equipment mod blueprints, one by one or a whole group. |
| Building resources | Fills what a station's build plans still miss, or adds a chosen ware to the build storage. |
| Activity | The last 60 host actions and their results. |

To move a flying pilot to another ship, use **Move to ship**. The server keeps
that ship for the next pilot to join, then kicks the pilot. When the pilot joins
again, the kept ship is the only one offered. Another pilot who joins first gets
it instead.

## Rules the server keeps

- World edits wait until the world is running with no save, trade, departure,
  fleet order or combat write pending. After 60 seconds of waiting, the edit
  fails and the page says so.
- The server's X4 changes at once. Pilots see world edits after their next
  join, because each join loads the newest checkpoint.
- **Save now** works only while nobody is connected. It saves the running world
  the same way the empty-world checkpoint does.
- The menu cannot create, destroy or transfer ships, because that would change
  the Company registry that every join checks.
- Every finished action is appended to `host-admin-audit.jsonl` in the run
  directory. The token is never written there.
- The page refuses requests whose `Host` or `Origin` is not a localhost
  address, and every API call needs the token.

## Files

- `tools/dev/authority_host_admin.py`: catalogue, X4 channel, job runner and web
  server.
- `tools/dev/authority_host_admin.lua` and `tools/dev/authority_host_admin.xml`:
  staged into the server's recovered world overlay at launch, like the purchase
  scripts.
- `tools/dev/authority_host_admin.html`: the page.

The ware lists and names come from the base game's own catalogues in the
`--game` directory.
