import pytz

from django.utils import timezone


class TimezoneMiddleware:
    """Render times in the member's local timezone.

    base.html sets the `django_timezone` cookie from the browser
    (Intl.DateTimeFormat) on every page load; this activates it for the
    request so aware datetimes display localized. Storage stays UTC.
    Falls back to the project default (UTC) when the cookie is missing or
    invalid (e.g. the very first request).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = request.COOKIES.get("django_timezone")
        if tzname:
            try:
                timezone.activate(pytz.timezone(tzname))
            except pytz.UnknownTimeZoneError:
                timezone.deactivate()
        else:
            timezone.deactivate()
        return self.get_response(request)
