import zoneinfo
import datetime
from .EventAutomation import ActionNetworkAutomation

DATE_TIME_FORMAT = "%Y-%m-%d %H:%M %Z"

def getTimeZoneNameFromDatetime(d) -> str:
    info = d.tzinfo
    if info is None:
        return None
    if hasattr(info, "zone") and info.zone is not None:
        return info.zone
    elif hasattr(info, "key") and info.key is not None:
        return info.key
    # Ugly hack but will work for now have issue to fix this whole timezone debacle better https://github.com/Austin-DSA/GeneralToolsWebsite/issues/26
    acceptedTimeZones = ActionNetworkAutomation.TimeZone.TZ_TO_AN_TZ.keys()

    target_offset = info.utcoffset()
    utc_instant = d.astimezone(datetime.timezone.utc)

    for zone in acceptedTimeZones:
        zi = zoneinfo.ZoneInfo(zone)
        if utc_instant.astimezone(zi).utcoffset() == target_offset:
            return zone
    
    raise Exception(f"Unkown UTC offset {target_offset} for datetime {d}")

    