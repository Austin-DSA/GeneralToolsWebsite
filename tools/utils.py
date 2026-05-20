from functools import wraps
from django.shortcuts import redirect
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
