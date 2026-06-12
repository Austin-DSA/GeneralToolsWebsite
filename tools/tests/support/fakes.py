from tools.WikiAutomation.OutlineAPI import OutlineAPI, OutlineConfig


class FakeOutline(OutlineAPI):
    """OutlineAPI with _call stubbed to canned responses keyed by method.

    A canned value that is an Exception instance is raised instead of returned
    (for failure-path tests). Calls are recorded on ``self.calls``.
    """

    def __init__(self, responses):
        super().__init__(OutlineConfig(baseUrl="https://wiki.example.org", apiToken="t"))
        self._responses = responses
        self.calls = []

    def _call(self, method, payload):
        self.calls.append((method, payload))
        response = self._responses[method]
        if isinstance(response, Exception):
            raise response
        return response
