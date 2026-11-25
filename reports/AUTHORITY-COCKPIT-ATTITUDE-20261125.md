# Cockpit position corrections preserve the observed ship's attitude

The receiving cockpit used to feed a local ship's yaw, pitch and roll
straight back into X4 when correcting its position. In the pinned X4 9.00
Linux and Windows executables, `GetObjectPositionInSector` returns radians
and `SetObjectSectorPos` accepts degrees. A 41° yaw would therefore become
about 0.72° on the first position write. The LAN client already converted
these angles. Commit `2ad3fc46` converts all three at the native setter
shared by ordinary writes and reacquisition.

In a disposable Linux X4 cockpit, archived motion samples moved WRN-302
by 16.59375 m. The fixture had set yaw to 41°, pitch to 17° and roll to
−23°. All 26 later native position readbacks across six distinct positions
retained the initial angles at getter precision. The pilot's own WOO-831
stayed in place. The marked source stayed intact and all owned processes
exited. An earlier attempt measured the initial attitude but issued no
playback writes because its archived replay omitted the local receipt
timestamp required by the receiver.

The native run exercised ordinary position writes in one Linux cockpit.
A LuaJIT test also exercised the larger reacquisition write. The Windows
evidence is static inspection of the two exported API functions. The V3
pilot stream still carries position and velocity. A remote pilot turning
in place is outside this result.

| Evidence | SHA-256 |
| --- | --- |
| Linux X4 executable | `113f73d74570084f6ce18a659b365009eccf579fb66dbabb5dad2b1718045f20` |
| Windows X4 executable | `19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891` |
| Private Linux run 02, client result | `ae7bcd0477674e33a641abf65c674595aba5a66837d84582bc4f96198dc9f2c7` |
| Private Linux run 02, X4 engine log | `ede4f2e46dc1ce26e9c73045f182972e1562ee516539413e78fd80cc352158b6` |
