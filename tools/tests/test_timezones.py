"""DateTimeWithAcceptedTimeZone: the value object that carries an event time as
an explicit (UTC instant, IANA zone name) pair instead of leaning on a
datetime's tzinfo.

The last block pins the EXACT tzinfo reads the three publish integrations do,
for both a summer (CDT) and a winter (CST) instant. Those call sites live in
Selenium/HTTP code that the task tests cannot reach, so this is where a
tzinfo-shape regression (issue #26) is caught.
"""
import datetime

import pytz
from django.test import TestCase

from tools.timezones import DateTimeWithAcceptedTimeZone


CHICAGO = pytz.timezone("America/Chicago")
SUMMER = CHICAGO.localize(datetime.datetime(2030, 7, 1, 18, 0))   # CDT, -05:00
WINTER = CHICAGO.localize(datetime.datetime(2030, 1, 15, 18, 0))  # CST, -06:00


class ValueObjectTests(TestCase):
    def test_serialized_form_is_naive_utc(self):
        dt = DateTimeWithAcceptedTimeZone.fromLocalized(SUMMER, "America/Chicago")
        # 18:00 CDT == 23:00 UTC, stored naive (no offset suffix).
        self.assertEqual(dt.utcNaiveIso(), "2030-07-01T23:00:00")
        self.assertNotIn("+", dt.utcNaiveIso())

    def test_round_trip_preserves_instant_and_zone(self):
        original = DateTimeWithAcceptedTimeZone.fromLocalized(WINTER, "America/Chicago")
        restored = DateTimeWithAcceptedTimeZone.fromUtcNaiveIso(
            original.utcNaiveIso(), "America/Chicago"
        )
        self.assertEqual(restored, original)
        self.assertEqual(restored.localized(), WINTER)

    def test_localized_is_pytz_named_zone(self):
        dt = DateTimeWithAcceptedTimeZone.fromLocalized(SUMMER, "America/Chicago")
        self.assertEqual(dt.localized().tzinfo.zone, "America/Chicago")

    def test_unknown_zone_fails_loud(self):
        with self.assertRaises(ValueError):
            DateTimeWithAcceptedTimeZone.fromLocalized(SUMMER, "Mars/Olympus_Mons")

    def test_naive_instant_is_treated_as_utc(self):
        dt = DateTimeWithAcceptedTimeZone.fromUtcNaiveIso(
            "2030-07-01T23:00:00", "America/Chicago"
        )
        self.assertEqual(dt.localized(), SUMMER)


class IntegrationContractTests(TestCase):
    """The exact tzinfo reads GoogleCalendarAPI / ActionNetworkAutomation / Zoom
    perform on the rehydrated datetime."""

    def _localized(self, localMoment):
        return DateTimeWithAcceptedTimeZone.fromLocalized(
            localMoment, "America/Chicago"
        ).localized()

    def test_google_calendar_zone_read(self):
        # GoogleCalendarAPI.convertDatetimeToDict: date.tzinfo.zone
        self.assertEqual(self._localized(SUMMER).tzinfo.zone, "America/Chicago")
        self.assertEqual(self._localized(WINTER).tzinfo.zone, "America/Chicago")

    def test_action_network_offset_read(self):
        # ActionNetworkAutomation.createEvent: startTime.strftime('%z')[1:3]
        self.assertEqual(self._localized(SUMMER).strftime("%z")[1:3], "05")
        self.assertEqual(self._localized(WINTER).strftime("%z")[1:3], "06")

    def test_zoom_tzname_read(self):
        # ZoomAPI: start.tzinfo.tzname(start)
        summer = self._localized(SUMMER)
        winter = self._localized(WINTER)
        self.assertEqual(summer.tzinfo.tzname(summer), "CDT")
        self.assertEqual(winter.tzinfo.tzname(winter), "CST")
