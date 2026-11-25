# Stock Docked menu Undock callback

An offline LuaJIT probe executed the Undock row assignment, dynamic colours,
permission predicate and handler from the installed X4 9.00 Docked menu.
The probe verified the menu entry's MD5
`cedebd1369740718f6de503597b69d4e` against the archive metadata and bytes.
It read the entry directly from `08.cat` / `08.dat` and kept the stock Lua in
memory. The row object, engine APIs, lock and server confirmation were simulated.

```sh
python tools/dev/company_stock_undock_click_probe.py '/path/to/X4 Foundations'
```

The stock row created after the replacement stored the replacement as its
click handler while the stock permission predicate reported false and the
background colour reported inactive. The row's Undock button properties left
`active` unspecified. A row created before replacement kept the original
handler, which refused departure under the simulated lock. The hook therefore
needs to be in place before the menu builds the row for a supported session.

Invoking the captured replacement twice while waiting produced one simulated
request and zero native dispatch calls. With an operation armed, a click on
another ship, berth or construction context fell through to the stock handler.
Stock departure or cancellation still depended on the stock handler's normal
guards, which refused dispatch while the simulated lock held. A denied initial
permission check did not arm a request, and stale confirmations did not clear
the simulated lock. Only the matching pending confirmation released it. Unarmed
clicks preserved stock departure and build cancellation. Cleanup restored the
menu handler and made retained replacement callbacks delegate to stock.

The probe did not launch X4. Whether the real UI dispatches a pointer click
while the stock colour is inactive remains a native verification item. The
probe did not exercise the keyboard shortcut or the live authority protocol.
