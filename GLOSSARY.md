# Release Monitoring

Watches upstream software repositories for new builds and relays each release to a Telegram channel.

## Language

**Watch item**:
One software project that is monitored for new builds.
_Avoid_: target, entry, job, monitor

**Detection strategy**:
The rule that decides when a watch item counts as updated.
`latest` follows the newest release — or, when a Tag filter is set, the newest release whose tag matches it; `tag` follows one named release whose files are replaced in place.
_Avoid_: checker, watcher type

**Tag filter**:
The optional pattern set that narrows `latest` to releases whose tag matches any pattern. Patterns use glob syntax and ignore case; a prerelease is never selected. The newest matching release is the one followed.
_Avoid_: tag mask, tag include, tag whitelist

**Marker**:
The value recorded after a successful delivery and compared against the upstream value on the next run to decide whether anything changed.
_Avoid_: version, fingerprint, last release

**Asset filter**:
The include/exclude expression that selects which release files are delivered.
Comma separates alternatives; plus requires every token.
_Avoid_: mask, pattern

**Delivery**:
Sending one watch item's notification, with its files, to the channel.
A watch item's marker is recorded only after its delivery succeeds.
_Avoid_: notification, message, push

**Consecutive failure**:
One run in which a watch item's detection or delivery failed, counted from its last success.
