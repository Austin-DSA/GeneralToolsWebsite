import datetime
import logging
import pytz
import typing
import dataclasses
import traceback

from . import ActionNetworkAutomation
from . import GoogleCalendarAPI
from . import ZoomAPI

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Conflict:
    class ConflictType:
        ZOOM = 0
        GCAL = 1

    type: int
    title: str
    start: datetime.datetime
    end: datetime.datetime
    zoomUser: typing.Optional[str]


@dataclasses.dataclass
class Result:
    class ResultType:
        PUBLISHED = 0
        UNRESOLVEABLE_CONFLICT = (
            1  # A conflict that can't be ignored, aka a zoom conflict
        )
        CONFLICT = 2  # A conflict that could be ignored if needed, like a gCal
        UNEXPECTED = 3

    type: int
    # Results if valid, if UNKOWN occurs, some of these results may be filled and the caller should check
    anManageLink: typing.Optional[str] = None
    anShareLink: typing.Optional[str] = None
    gCalLink: typing.Optional[str] = None
    zoomLink: typing.Optional[str] = None
    zoomAccount: typing.Optional[str] = None

    # Results if there is a conflict
    conflicts: typing.List[Conflict] = dataclasses.field(default_factory=list)

    # Error for unexpected
    errorStr: typing.Optional[str] = None

    def valid(self) -> bool:
        return self.type == Result.ResultType.PUBLISHED


@dataclasses.dataclass
class EventInfo:
    title: str
    start: datetime.datetime
    end: datetime.datetime

    locationName: str
    streetAddress: str
    city: str
    state: str
    zip: str

    description: str
    instructions: str = ""
    country: str = "US"


@dataclasses.dataclass
class Config:
    zoomConfig: ZoomAPI.ZoomConfig
    anConfig: ActionNetworkAutomation.ANAutomatorConfig
    gCalConfig: GoogleCalendarAPI.GoogleCalendarConfig
    # This should be used to force a publish after showing the user the potential conflicts
    ignoreResolveableConflicts: bool = False


# TODO: Handle partial results
# Shouldn't throw an exception
def publishEvent(eventInfo: EventInfo, config: Config) -> Result:
    result = Result(type=-1)
    try:
        # Guards
        if (
            eventInfo.start.tzinfo is None
            or eventInfo.start.tzinfo.utcoffset(eventInfo.start) is None
        ):
            logger.error(
                "EventPublisher: The argument for the eventInfo.start  must be timezone aware. Passed in unaware object."
            )
            raise Exception(
                "EventPublisher: The argument for the  eventInfo.start must be timezone aware. Passed in unaware object."
            )
        if (
            eventInfo.end.tzinfo is None
            or eventInfo.end.tzinfo.utcoffset(eventInfo.end) is None
        ):
            logger.error(
                "EventPublisher: The argument for the eventInfo.end  must be timezone aware. Passed in unaware object."
            )
            raise Exception(
                "EventPublisher: The argument for the  eventInfo.end must be timezone aware. Passed in unaware object."
            )
        if eventInfo.end < eventInfo.start:
            logger.error("EventPublisher: eventInfo.end must be after eventInfo.start")
            raise Exception(
                "EventPublisher: eventInfo.end must be after eventInfo.start"
            )

        # Check for conflicts on Zoom
        zoomApi = ZoomAPI.ZoomAPI(config.zoomConfig)
        availablility = zoomApi.getAccountsAndAvailablilityForTime(
            eventInfo.start, eventInfo.end - eventInfo.start
        )
        zoomAccount = None
        zoomConflicts = []
        for (account, conflicts) in availablility:
            if len(conflicts) == 0:
                logger.info(
                    "EventPublisher: Found available zoom account %s", account.email
                )
                zoomAccount = account
                break
            zoomConflicts.extend(
                [
                    Conflict(
                        type=Conflict.ConflictType.ZOOM,
                        title=c.topic,
                        start=c.startTime,
                        end=c.startTime + c.duration,
                        zoomUser=account.email,
                    )
                    for c in conflicts
                ]
            )

        # Check for conflicts on Google
        gCalAPI = GoogleCalendarAPI.GoogleCalendarAPI(config.gCalConfig)
        conflicts = gCalAPI.findConflicts(
            eventInfo.start, eventInfo.end - eventInfo.start
        )
        gCalConflicts = [
            Conflict(
                type=Conflict.ConflictType.GCAL,
                title=c.title,
                start=c.start,
                end=c.end,
                zoomUser=None,
            )
            for c in conflicts
        ]

        # TODO: Should we combine zoom and google conflicts for unresolveable???
        # A Zoom conflict is unresolveable
        if zoomAccount is None:
            logger.error(
                "EventPublisher: Found unresolveable zoom conflicts or no zoom account %s ",
                str(zoomConflicts),
            )
            result.type = Result.ResultType.UNRESOLVEABLE_CONFLICT
            result.conflicts = zoomConflicts
            return result
        if len(gCalConflicts) > 0 and not config.ignoreResolveableConflicts:
            logger.error("EventPublisher: Found gCal conflicts %s ", str(gCalConflicts))
            result.type = Result.ResultType.CONFLICT
            result.conflicts = gCalConflicts
            return result

        # Schedule Zoom Meeting
        zoomLink = zoomApi.createMeeting(
            title=eventInfo.title,
            start=eventInfo.start,
            duration=eventInfo.end - eventInfo.start,
            user=zoomAccount,
        )
        result.zoomLink = zoomLink
        result.zoomAccount = zoomAccount.email
        # Schedule Action Network
        anEventConfirmInfo = ActionNetworkAutomation.ANAutomator.createEvent(
            eventInfo=ActionNetworkAutomation.EventInfo(
                title=eventInfo.title,
                startTime=eventInfo.start,
                endTime=eventInfo.end,
                locationName=eventInfo.locationName,
                address=eventInfo.streetAddress,
                city=eventInfo.city,
                state=eventInfo.state,
                zip=eventInfo.zip,
                description=eventInfo.description,
                country=eventInfo.country,
                insturctions=f"Zoom: {zoomLink} \n\n {eventInfo.instructions}",
            ),
            config=config.anConfig,
        )
        result.anManageLink = anEventConfirmInfo.manageLink
        result.anShareLink = anEventConfirmInfo.directLink
        # Schedule Google Calendar
        gCalLink = gCalAPI.createEvent(
            GoogleCalendarAPI.Event(
                title=eventInfo.title,
                start=eventInfo.start,
                end=eventInfo.end,
                description=f"RSVP: {anEventConfirmInfo.directLink} \n\n {eventInfo.description}",
                location=f"{eventInfo.streetAddress}, {eventInfo.city}, {eventInfo.state} {eventInfo.zip}",
            )
        )
        result.gCalLink = gCalLink
        result.type = Result.ResultType.PUBLISHED
        return result

    except Exception as e:
        result.type = Result.ResultType.UNEXPECTED
        result.errorStr = traceback.format_exception(e)
        return result
