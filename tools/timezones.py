"""One canonical representation of an event time.

A "local event time" is really two independent facts: the literal wall-clock
time a user entered (e.g. 6:00 PM) and the IANA time zone it is read in (e.g.
"America/Chicago"). A bare ``datetime`` cannot carry both across a
serialization boundary: ``datetime.isoformat()`` preserves only the numeric
offset, and ``datetime.fromisoformat()`` hands back a fixed-offset ``tzinfo``
that has no zone name at all (no ``.zone`` like pytz, no ``.key`` like
zoneinfo). Every timezone bug in this app has come from reading the zone back
off a datetime's ``tzinfo`` after such a round trip (see GitHub issue #26).

``DateTimeWithAcceptedTimeZone`` stores the two facts explicitly: the literal
wall time (naive) plus the validated zone name. It serializes from them
directly, never from ``tzinfo``, so both survive a round trip losslessly and we
store exactly what the user entered. UTC is derived on demand via :meth:`utc`.

DST note: converting a wall time to an absolute instant is the only step where
ambiguity can arise. A fall-back wall time maps to two instants and a
spring-forward wall time to none. That resolution happens in :meth:`localized`
/ :meth:`utc` via pytz's ``localize`` (default ``is_dst`` picks the
standard-time side); it is the single, documented place the fold is decided.
Storage and display never trigger it, because they carry the wall time as-is.
"""
import datetime

import pytz


def _requireAcceptedZone(zoneName: str) -> None:
    if zoneName not in pytz.all_timezones_set:
        raise ValueError(
            f"DateTimeWithAcceptedTimeZone: unknown time zone {zoneName!r}"
        )

TZ_TO_AN_TZ = {
        'US/Central': "Central",
        'US/Eastern': "Eastern",
        'US/Mountain': "Mountain",
        'US/Pacific' : "Pacific"
    }

TZ_TO_ZOOM_TZ = {
    'US/Central': "America/Chicago",
    'US/Eastern': "America/New_York",
    'US/Mountain': "America/Denver",
    'US/Pacific' : "America/Los_Angeles"
}

class DateTimeWithAcceptedTimeZone:
    """A literal wall time plus the accepted IANA zone it is read in.

    Construct from an already-localized datetime via :meth:`fromLocalized`, or
    from a serialized wall-time ISO string via :meth:`fromWallIso`.
    """

    def __init__(self, wallTime: datetime.datetime, zoneName: str):
        _requireAcceptedZone(zoneName)
        if wallTime.tzinfo is not None:
            raise ValueError(
                "DateTimeWithAcceptedTimeZone: wallTime must be naive (a literal "
                "local clock reading); pass zone separately"
            )
        self._wall = wallTime
        self._zoneName = zoneName

    @classmethod
    def fromLocalized(
        cls, localizedDateTime: datetime.datetime, zoneName: str
    ) -> "DateTimeWithAcceptedTimeZone":
        """Build from a datetime and its separately-known zone name.

        The zone name is passed explicitly rather than read off
        ``localizedDateTime.tzinfo`` on purpose: that read is exactly the
        fragile step this type exists to remove. An aware input is converted to
        the accepted zone first so the stored wall time reads in that zone.
        """
        _requireAcceptedZone(zoneName)
        if (localizedDateTime.tzinfo is not None
                and localizedDateTime.tzinfo.utcoffset(localizedDateTime) is not None):
            localizedDateTime = localizedDateTime.astimezone(pytz.timezone(zoneName))
        return cls(localizedDateTime.replace(tzinfo=None), zoneName)

    @classmethod
    def fromWallIso(
        cls, wallIso: str, zoneName: str
    ) -> "DateTimeWithAcceptedTimeZone":
        """Rebuild from the serialized form written by :meth:`wallIso`."""
        return cls(datetime.datetime.fromisoformat(wallIso), zoneName)

    @property
    def zoneName(self) -> str:
        return self._zoneName

    @property
    def wallTime(self) -> datetime.datetime:
        """Returns the naiive walltime"""
        return self._wall

    def localized(self) -> datetime.datetime:
        """The wall time as an aware, pytz-localized datetime.

        pytz-backed, so ``.tzinfo.zone`` is the IANA name and DST is applied
        for the instant. This is the value the Google Calendar, Action Network,
        and Zoom integrations consume. See the module DST note for the fold.
        """
        return pytz.timezone(self._zoneName).localize(self._wall)

    def utc(self) -> datetime.datetime:
        """The same instant as an aware UTC datetime (fold resolved here)."""
        return self.localized().astimezone(pytz.utc)

    def wallIso(self) -> str:
        """The literal wall time as a naive ISO string, for serialization.

        The zone name is serialized separately and never inferred from this
        string, which is what keeps the round trip lossless and preserves
        exactly what the user entered.
        """
        return self._wall.isoformat()

    def __eq__(self, other) -> bool:
        if not isinstance(other, DateTimeWithAcceptedTimeZone):
            return NotImplemented
        return self._wall == other._wall and self._zoneName == other._zoneName

    def __repr__(self) -> str:
        return (
            f"DateTimeWithAcceptedTimeZone("
            f"{self._wall.isoformat()}, {self._zoneName!r})"
        )
    
    def toDict(self) -> dict[str,str]:
        return {
            "wall" : self._wall.isoformat(),
            "zoneName" : self._zoneName
        }
    
    @classmethod
    def fromDict(cls, d: dict) -> "DateTimeWithAcceptedTimeZone":
        return cls.fromWallIso(wallIso=d["wall"], zoneName=d["zoneName"])

    def prettyString(self) -> str:
        return f'{self._wall.strftime("%Y-%m-%d %H:%M")} ({self._zoneName})'
