# Cold rejoin after a confirmed Company purchase

On Bazzite, a fresh native Linux pilot rejoined WRN-302 after the
authority restarted from the confirmed post-purchase world. After
taking the helm, X4 itself reported four units of water aboard and
976,800 cents in both the player and Company accounts. The native query
also verified PYN-740's original captain seed, assignment and presence
aboard the same ship at BIM-169.
[Preceding native purchase](AUTHORITY-STOCK-PURCHASE-NATIVE-PAIR-20261125.md)

The authority started from an isolated copy of the preceding run's
confirmed departure checkpoint, including its manifest and verified
purchase-ledger sidecar. The ordinary launchers ran on one Bazzite
machine with separate private displays and pinned loopback TLS. The
pilot loaded the fresh offer and X4 reported that offer's marker.
The read-only query ran once after the normal helm confirmation. It
opened no trade menu and sent no purchase request.

| World stage | Checkpoint | Save SHA-256 |
| --- | --- | --- |
| Confirmed purchase source | `checkpoint-bd61fcd29661453ebc1fc8e29b372dfa` | `8030d414d1e1e1cf133cc8e09f8c6d9ea87197038a1ebc73c6c5552da080c8a2` |
| Fresh offer loaded by the returning pilot | `checkpoint-a5054041840d48f09a7334e049a6ccf7` | `544093ecf94e9b7b742f5c3f1d54859d5e8f2f9168665c622938b3b434f9de6d` |
| Confirmed after the returning pilot left | `checkpoint-4510a9c0afab47c586ca5f7eb613d25a` | `c26b6ea00fcd5d542387c2a79afd5e41e7e9bdade3d93b69928979d316d2a459` |

X4 reported offer marker `9c98a3bbd34f7987f53d0a05464a550e` during load
and returned WRN-302's account and cargo at game age 345.80 s. The offer
and the second departure saved the same Company balance and cargo as
the purchase source. All three purchase rows matched exactly across
the three ledger sidecars. Both departure pointers agreed, both games
exited with no owned processes remaining, and the original input and
its copy retained their hashes.

This establishes persisted economy on a fresh load. Live in-session
economy replication and Internet or Windows behavior were outside the
test. Private verification result SHA-256
`b417e97b8e76d38cd1912b1a1e5853efaad9a7b826fcbe5efeb6ccf26a3ec220`.
