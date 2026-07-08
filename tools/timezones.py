"""One canonical representation of an event time.

A "local event time" is really two independent facts: an absolute instant (in
UTC) and the IANA time zone it should be read in (e.g. "America/Chicago"). A
bare ``datetime`` cannot carry both across a serialization boundary:
``datetime.isoformat()`` preserves only the numeric offset, and
``datetime.fromisoformat()`` hands back a fixed-offset ``tzinfo`` that has no
zone name at all (no ``.zone`` like pytz, no ``.key`` like zoneinfo). Every
timezone bug in this app has come from reading the zone back off a datetime's
``tzinfo`` after such a round trip (see GitHub issue #26).

``DateTimeWithAcceptedTimeZone`` keeps the two facts explicit and serializes
from them directly, never from ``tzinfo``. The zone NAME is the source of
truth; it is validated on construction and stored as a string, so it always
survives a round trip. Callers get a fully localized, pytz-aware datetime back
from :meth:`localized`, which is what the downstream integrations (Google
Calendar, Action Network, Zoom) expect.
"""
import datetime

import pytz


class DateTimeWithAcceptedTimeZone:
    """An absolute instant plus the accepted IANA time zone it is read in.

    The instant is held as an aware UTC datetime; the zone as a validated IANA
    name string. Construct from a localized datetime via :meth:`fromLocalized`,
    or from a serialized naive-UTC ISO string via :meth:`fromUtcNaiveIso`.
    """

    def __init__(self, instant: datetime.datetime, zoneName: str):
        if zoneName not in pytz.all_timezones_set:
            raise ValueError(
                f"DateTimeWithAcceptedTimeZone: unknown time zone {zoneName!r}"
            )
        # Normalize the instant to aware UTC. A naive instant is taken to be in
        # UTC (the same naive-means-UTC guard models.getStartLocalized uses);
        # the zone name, not this tzinfo, decides how the instant is displayed.
        if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
            instant = pytz.utc.localize(instant)
        self._instantUtc = instant.astimezone(pytz.utc)
        self._zoneName = zoneName

    @classmethod
    def fromLocalized(
        cls, localizedDateTime: datetime.datetime, zoneName: str
    ) -> "DateTimeWithAcceptedTimeZone":
        """Build from an aware local datetime and its separately-known zone name.

        The zone name is passed explicitly rather than read off
        ``localizedDateTime.tzinfo`` on purpose: that read is exactly the
        fragile step this type exists to remove.
        """
        return cls(localizedDateTime, zoneName)

    @classmethod
    def fromUtcNaiveIso(
        cls, utcNaiveIso: str, zoneName: str
    ) -> "DateTimeWithAcceptedTimeZone":
        """Rebuild from the serialized form written by :meth:`utcNaiveIso`."""
        naive = datetime.datetime.fromisoformat(utcNaiveIso)
        return cls(naive, zoneName)

    @property
    def zoneName(self) -> str:
        return self._zoneName

    def localized(self) -> datetime.datetime:
        """An aware datetime at this instant, in the accepted zone.

        pytz-backed, so ``.tzinfo.zone`` is the IANA name and DST is applied
        correctly for the instant. This is the value the Google Calendar,
        Action Network, and Zoom integrations consume.
        """
        return self._instantUtc.astimezone(pytz.timezone(self._zoneName))

    def utcNaiveIso(self) -> str:
        """The instant as a naive-UTC ISO string, for serialization.

        The zone name is serialized separately and never inferred from this
        string, which is what keeps the round trip lossless.
        """
        return self._instantUtc.replace(tzinfo=None).isoformat()

    def __eq__(self, other) -> bool:
        if not isinstance(other, DateTimeWithAcceptedTimeZone):
            return NotImplemented
        return (
            self._instantUtc == other._instantUtc
            and self._zoneName == other._zoneName
        )

    def __repr__(self) -> str:
        return (
            f"DateTimeWithAcceptedTimeZone("
            f"{self._instantUtc.isoformat()}, {self._zoneName!r})"
        )
