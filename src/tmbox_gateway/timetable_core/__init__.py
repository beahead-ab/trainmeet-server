"""Tidtabellens kärna, delad mellan TrainMeet Cloud och TrainMeet Server.

Cloud bygger driftpaketet med den här koden, och servern bygger om tjänster
och rutter med exakt samma kod när en tidtabell ändras på plats. Därför är
katalogen en byte-identisk kopia i båda repona:

- trainmeet-cloud/cloud/timetable_core/
- trainmeet-server/src/tmbox_gateway/timetable_core/

Ett digestprov i båda repona låser innehållet. Ändra här i Cloud, kopiera
med tools/sync-timetable-core.mjs i servern och flytta digesten i båda.
Koden använder bara Pythons standardbibliotek och sina egna moduler.
"""
from .dispatch import dispatch_mode
from .findings import ENGINE_VERSION, check_draft
from .sanity import check_sanity
from .services import build_services, service_groups, service_stops
from .times import canonical_days, clock_minutes, normalized, parse_clock, safe_id
from .tracks import find_track, resolve_row_track, track_catalogue

__all__ = [
    "ENGINE_VERSION", "build_services", "canonical_days", "check_draft", "check_sanity", "clock_minutes",
    "dispatch_mode", "find_track", "normalized", "parse_clock",
    "resolve_row_track", "safe_id", "service_groups", "service_stops", "track_catalogue",
]
