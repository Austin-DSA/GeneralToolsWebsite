import datetime

from django.test import TestCase

from tools.LinkTree import tracking


# --- tracking (privacy-first helpers) --------------------------------------


class TrackingHelperTests(TestCase):
    def test_visitor_hash_is_deterministic_per_day(self):
        day = datetime.date(2026, 5, 31)
        h1 = tracking.visitorHash("203.0.113.5", "UA/1.0", "salt", day=day)
        h2 = tracking.visitorHash("203.0.113.5", "UA/1.0", "salt", day=day)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_visitor_hash_rotates_daily_and_hides_ip(self):
        ip = "203.0.113.5"
        d1 = tracking.visitorHash(ip, "UA/1.0", "salt", day=datetime.date(2026, 5, 31))
        d2 = tracking.visitorHash(ip, "UA/1.0", "salt", day=datetime.date(2026, 6, 1))
        self.assertNotEqual(d1, d2, "hash must rotate across days")
        # The raw IP must not be recoverable/visible in the digest.
        self.assertNotIn("203.0.113.5", d1)

    def test_ua_family_is_coarse(self):
        self.assertEqual(
            tracking.uaFamily("Mozilla/5.0 (iPhone) AppleWebKit Safari"), "mobile-safari"
        )
        self.assertEqual(tracking.uaFamily("Mozilla/5.0 (Windows) Chrome/120"), "desktop-chrome")
        self.assertEqual(tracking.uaFamily("Googlebot/2.1"), "bot")
        self.assertEqual(tracking.uaFamily(""), "")

    def test_referrer_host_strips_path(self):
        self.assertEqual(
            tracking.referrerHost("https://twitter.com/austin_dsa/status/123"), "twitter.com"
        )
        self.assertEqual(tracking.referrerHost(""), "")

    def test_client_ip_prefers_forwarded_for(self):
        meta = {"HTTP_X_FORWARDED_FOR": "198.51.100.7, 10.0.0.1", "REMOTE_ADDR": "10.0.0.1"}
        self.assertEqual(tracking.clientIpFromMeta(meta), "198.51.100.7")
        self.assertEqual(tracking.clientIpFromMeta({"REMOTE_ADDR": "10.0.0.1"}), "10.0.0.1")
