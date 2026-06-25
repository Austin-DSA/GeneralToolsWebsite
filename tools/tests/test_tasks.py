from unittest import mock

from django.test import SimpleTestCase

from tools import tasks


# --- Huey background tasks (tools/tasks.py) ---------------------------------
#
# The HUEY setting puts huey in immediate mode under tests ("test" in
# sys.argv), so nothing here needs a consumer. call_local() invokes the
# task's underlying function directly.


class SyncLinkTreeWikiTaskTests(SimpleTestCase):
    def test_calls_command_quietly(self):
        with mock.patch("tools.tasks.call_command") as mockCall:
            tasks.syncLinkTreeWiki.call_local()
        mockCall.assert_called_once_with("sync_link_tree_wiki", quiet=True)

    def test_error_exit_from_command_is_swallowed_and_logged(self):
        # The command raises SystemExit(1) for host schedulers; inside the
        # consumer that must become a logged error, never an exit.
        with mock.patch("tools.tasks.call_command", side_effect=SystemExit(1)):
            with self.assertLogs("tools.tasks", level="ERROR"):
                tasks.syncLinkTreeWiki.call_local()

    def test_clean_exit_from_command_is_silent(self):
        with mock.patch("tools.tasks.call_command", side_effect=SystemExit(0)):
            with self.assertNoLogs("tools.tasks", level="ERROR"):
                tasks.syncLinkTreeWiki.call_local()
