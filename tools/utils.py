
DATE_TIME_FORMAT = "%Y-%m-%d %H:%M %Z"

def getTimeZoneNameFromDatetime(datetime) -> str:
    info = startTime.tzinfo
    if info is None:
        return None
    if hasattr(info, "zone") and info.zone is not None:
        return info.zone
    elif hasattr(info, "key") and info.key is not None:
        return info.key
    raise Exception("Timezone object has neither key nor zone")
    
    