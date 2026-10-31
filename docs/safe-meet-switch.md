# Explicitly leaving a meet

Issue: https://github.com/beahead-ab/trainmeet-server/issues/217

Entering a connection code first previews the Cloud package without claiming a server connection. When the package belongs to a different meet, the API returns `409 meet_change_required` with the current lifecycle selection, the name to type and the destination name. The browser shows a danger dialog and offers a copy button for the current name.

A confirmed retry includes `confirm_meet_change: true`, the complete `leaving_selection` and exact `leaving_name`. The API compares these under the lifecycle lock. Wrong, absent or stale confirmation does not claim a Cloud connection or change traffic. Cancel does not send another request.

After validation, a database backup is taken before switching. A confirmed switch may end live traffic and reset the local engine even while the clock runs. EU trains start at the destination plan's start time with the clock paused, old station assignments are cleared, and US sessions left behind are closed with outstanding warrants voided. A previously used EU destination is reset after the backup rather than silently resurrecting its old state. User accounts, server identity and network settings remain.

Same-meet configuration updates retain their existing traffic guards and do not reset the running session. The durable lifecycle transition marker still blocks traffic if activation is interrupted.

Cloud must be updated first to support read-only code previews, installation credentials and package checksums. See `trainmeet-cloud/docs/server-connection-identity.md` for the companion protocol. Server retries are idempotent in Cloud. Old unidentifiable duplicate rows require the existing explicit remove action, because equal display names are not evidence of equal installations.
