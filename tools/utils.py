
DATE_TIME_FORMAT = "%Y-%m-%d %H:%M %Z"

# Datetimes are stored UTC (settings.TIME_ZONE = "UTC"), but chapter-facing dates
# (a resolution's filing deadline, its effective date, the archive record) must
# read in local chapter time. Meetings carry their own timezone; this is the
# fallback when there is no meeting to borrow one from.
CHAPTER_TIME_ZONE = "America/Chicago"