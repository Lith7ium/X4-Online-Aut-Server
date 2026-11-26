# 47-hit Company burst replay over product TLS

On 26 November 2026, one native X4 9.00 provider on Bazzite ran
`fa32012e` with `--trial-ordered-gun-hits`. Two scripted pilots
obtained separate Company admissions and motion grants using the regular
combat route over pinned loopback TLS. The provider recorded the pinned
Python module hashes and verified the staged native MD and Lua before
starting X4. Fedora did not host this run.

The request data came from 47 successive 26-point root-shield debits in
the earlier native shooter log, spanning 0.5869 game seconds. The replay
supplied those observations as scripted contacts. The earlier capture
forwarded one direct contact, and this replay does not establish a
47-hit shooter capture path. [Earlier two-cockpit evidence](AUTHORITY-TWO-COCKPIT-COMBAT-20261126.md)

| Frame | Confirmed orders | Game age | Native adapter elapsed | TLS exchange elapsed | Shield / hull after |
| --- | --- | ---: | ---: | ---: | ---: |
| 32 contacts | 1–32 | 4.72783 s | 0.553 s, 57.9 contacts/s | 0.788 s, 40.6 contacts/s | 1,534 / 3,600 |
| 15 contacts | 33–47 | 5.62730 s | 0.271 s, 55.3 contacts/s | 0.504 s, 29.7 contacts/s | 1,144 / 3,600 |

The first TLS reply reached the client and decoded successfully. The
test driver withheld application completion and closed that combat
stream. The ordinary client reconnected and sent the exact pending
frame again. It received equal receipts in 0.113 s. A later exact retry
of the second frame also returned equal receipts, in 0.112 s. The
adapter recorded two groups and 47 native rows total. Application
completion loss was simulated on a functioning network.

B's fresh native health read moved from 2,366 shield and 3,600 hull
at revision 0 to 1,144 shield and 3,600 hull at revision 47. After the
second retry, the same-revision query returned no update and a fresh
combat connection read the same health and revision. The provider
exited 0, paused after two admissions and 26 motion samples. A separate
save check verified the marker, registry and 1,144 shield with full
3,600 hull at last departure. The provider left no X4 process or engine
lock.

| Evidence | SHA-256 |
| --- | --- |
| Earlier audited shooter trace | `94ce09d68b2465134e5f29b56858fd7ef72c8e22064c18cb37b38a487e3d00ab` |
| Replay input | `57ce6eaa18903656dbaa1736ee3b4dd9f76b1f4b5b5c1ea0378e680ee18648f1` |
| Preserved source save | `1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958` |
| Confirmed departure `checkpoint-a50ece9108b04d6696ea936077b6cdbe` | `a95f762b369cd11cd50575344de43fdfe1c97aab9b7b50a161ca3a1abf139f2a` |

The preceding Bazzite attempt confirmed 32 impacts and saved 1,534
shield. A private assertion had required exact integer HP and stopped
at receipt 23 on a sub-micro-HP difference. The successful run uses
the native comparison tolerance for HP and exact field equality for
retries. Its private evidence is in
`.local/company-replay47-local/replay47-local-03/`.

## Receipt delivery after repeated connection loss

A Linux loopback regression exercised the regular sender over pinned TLS with a simulated X4 damage adapter. The server applied two 26-point hits, then the test closed three successive connections before the sender could receive their receipts. The previous sender stopped. With reconnects spaced out within the original acknowledgement deadline, the fourth connection returned the original orders 1 and 2. The same pilot then fired again and received order 3. The adapter counted exactly three writes, leaving zero shield and 3,552 hull from an initial 30 shield and 3,600 hull. A connection that stays open without delivering a receipt can still reach the original deadline. [TLS regression](../tests/test_authority_company_combat_tls.py)

## Direct contacts from two stock primary guns

The development client accepts direct root-ship hits from an unmodified S Pulse Laser Mk1 as well as the S Bolt Repeater Mk2. The installed vanilla `05.cat` and `05.dat` define the Pulse projectile as `bullet_gen_s_laser_01_mk1_macro`, with one projectile, one possible hit and 36 general damage. The observer checks the mounted gun, the projectile macro and the damage profile in the local game. The Bolt retains its original 26-point contact format.

In a Linux loopback test, one ordinary sender reported a Pulse hit and a Bolt hit on a target with 80 shield and 2,000 hull. A second sender reported another Pulse hit on the same target. The simulated damage adapter received 36, 26 and 36 in that order and returned a final 0 shield and 1,982 hull. A LuaJIT test exercised the cockpit callback with simulated X4 getters. A live Pulse hit from an admitted X4 cockpit and the other pilot's view of the NPC remain to be verified. [Cockpit source checks](../tests/test_authority_company_combat_source.py) · [Two-pilot TLS test](../tests/test_authority_company_combat_tls.py)
