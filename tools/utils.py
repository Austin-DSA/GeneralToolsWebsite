import datetime
import pytz
from functools import wraps
from django.shortcuts import redirect
import django.utils.timezone

DATE_TIME_FORMAT = "%Y-%m-%d %H:%M %Z"

def sessionDataRequired(sessionKeys : list[str], redirectURL: str):
    def decorator(viewFunc):
        @wraps(viewFunc)
        def wrapper(request, *args, **kwargs):
            for sessionKey in sessionKeys:
                if sessionKey not in request.session:
                    return redirect(redirectURL)
            return viewFunc(request, *args, **kwargs)
        return wrapper
    return decorator

def localizeDate(utcTime: datetime.datetime, timezoneStr: str):
    # If naiive add in the UTC info
    if django.utils.timezone.is_naive(utcTime):
        utcTime = django.utils.timezone.make_aware(utcTime, timezone=django.utils.timezone.utc)

    timezone = pytz.timezone(self.timezone)
    localTime = django.utils.timezone.localtime(utcTime, timezone=timezone)
    return localTime
