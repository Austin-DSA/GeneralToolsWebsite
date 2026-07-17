import datetime
import google.auth
import google.auth.transport
import google.auth.transport.requests
import google.oauth2.service_account
import googleapiclient.discovery
import logging
import dataclasses
import typing
import pytz
import os

from ..timezones import DateTimeWithAcceptedTimeZone

logger = logging.getLogger(__name__)


class Constants:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]
    CALENDAR_SEVRVICE = "calendar"
    CALENDAR_SERVICE_VERSION = "v3"

    class EventKeys:
        DESCRIPTION = "description"
        TITLE = "summary"
        LOCATION = "location"
        START = "start"
        END = "end"
        LINK = "htmlLink"

        class Date:
            TIME = "dateTime"
            TIMEZONE = "timeZone"


@dataclasses.dataclass
class Event:
    title: str
    start: DateTimeWithAcceptedTimeZone
    end: DateTimeWithAcceptedTimeZone  # Technically end is optional but we will require all events have a specific end
    description: str
    location: str | None
    link: typing.Optional[
        str
    ] = None  # Not writeable so we don't need to serialize to the API just from

    @staticmethod
    def convertDatetimeToDict(date: DateTimeWithAcceptedTimeZone) -> dict:
        return {
            Constants.EventKeys.Date.TIME: date.wallIso,
            Constants.EventKeys.Date.TIMEZONE: date.zoneName,
        }

    @staticmethod
    def convertDictToDatetime(d: dict) -> DateTimeWithAcceptedTimeZone:
        time = datetime.datetime.fromisoformat(d[Constants.EventKeys.Date.TIME])
        if Constants.EventKeys.Date.TIMEZONE in d:
            timezone = d[Constants.EventKeys.Date.TIMEZONE]
        else:
            # If no timezone given assume UTC
            timezone = "UTC"
        return DateTimeWithAcceptedTimeZone.fromLocalized(localizedDateTime=time, zoneName=timezone)

    @staticmethod
    def fromApiDict(d: dict):
        title = d[Constants.EventKeys.TITLE]
        description = d[Constants.EventKeys.DESCRIPTION]
        start = Event.convertDictToDatetime(d[Constants.EventKeys.START])
        end = Event.convertDictToDatetime(d[Constants.EventKeys.END])
        location = None
        if Constants.EventKeys.LOCATION in d:
            location = d[Constants.EventKeys.LOCATION]
        link = None
        if Constants.EventKeys.LINK in d:
            link = d[Constants.EventKeys.LINK]
        return Event(title, start, end, description, location, link)

    def toApiDict(self) -> dict:
        d = {
            Constants.EventKeys.DESCRIPTION: self.description,
            Constants.EventKeys.TITLE: self.title,
            Constants.EventKeys.START: Event.convertDatetimeToDict(self.start),
            Constants.EventKeys.END: Event.convertDatetimeToDict(self.end),
        }
        if self.location is not None:
            d[Constants.EventKeys.LOCATION] = self.location
        return d


@dataclasses.dataclass
class GoogleCalendarConfig:
    serviceKeyPath: str
    calendarId: str
    delegateAccount: str


# https://github.com/googleapis/google-api-python-client/blob/main/docs/start.md
# https://googleapis.github.io/google-api-python-client/docs/dyn/calendar_v3.html
# Delegation Auth- https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority
# For auth:
# 1. Create Project Here: https://console.cloud.google.com/apis/
# 2. Enable calendar API
# 3. Create Service Account
# 4. Enable Delegation Wide Authority to Service Account in Admin Console
# 5. Choose an account it can delegate that has access to calendar
class GoogleCalendarAPI:
    def __init__(self, config: GoogleCalendarConfig):
        logger.info("GoogleCalendarAPI: Logging in with provided credential file %s", config.serviceKeyPath)
        if not os.path.exists(config.serviceKeyPath):
            logger.error(
                "GoogleCalendarAPI: Service Key path does not exist %s",
                config.serviceKeyPath,
            )
            raise Exception(
                f"GoogleCalendarAPI: Service Key path does not exist {config.serviceKeyPath}"
            )
        self.config = config
        self.serviceAccountCreds = (
            google.oauth2.service_account.Credentials.from_service_account_file(
                self.config.serviceKeyPath, scopes=Constants.SCOPES
            )
        )
        self.delegatedCreds = self.serviceAccountCreds.with_subject(
            self.config.delegateAccount
        )
        self.delegatedCreds.refresh(google.auth.transport.requests.Request())
        # Unclear from docs if we need to refresh these
        # if not self.delegatedCreds or not self.delegatedCreds.valid:
        #     logger.error("GoogleCalendarAPI: Could not create credentials")
        #     raise Exception("GoogleCalendarAPI: Could not create credentials")

    # https://googleapis.github.io/google-api-python-client/docs/dyn/calendar_v3.events.html#list
    def findConflicts(
        self, start: DateTimeWithAcceptedTimeZone, duration: datetime.timedelta
    ) -> list[Event]:
        # Require timezone aware objects

        # Give 15 min runway between events
        # Construct end before start since end references start and the names are overloaded
        end = DateTimeWithAcceptedTimeZone(wallTime=start.wallTime+duration+datetime.timedelta(minutes=15), zoneName=start.zoneName)
        start = DateTimeWithAcceptedTimeZone(wallTime=start.wallTime-datetime.timedelta(minutes=15), zoneName=start.zoneName)
        logger.info(
            "GoogleCalendarAPI: Looking for conflicts from %s to %s",
            str(start),
            str(end),
        )
        with googleapiclient.discovery.build(
            Constants.CALENDAR_SEVRVICE,
            Constants.CALENDAR_SERVICE_VERSION,
            credentials=self.delegatedCreds,
        ) as service:
            result = []
            pageToken = None
            while True:
                # It isn't clear from the docs how the timezones work here
                # It says the timeMin and timeMax need timezone offsets in their strings
                # It thens says the timezone argument is used for the response
                # I'm reading this as the timezones don't matter for what we send in as long as it is defined
                # Then it will return localized times
                # So I'm going to send in UTC since isoformat for localized times is janky, but request the return time to be whatever timezone was passed in
                response = (
                    service.events()
                    .list(
                        calendarId=self.config.calendarId,
                        timeMin=start.utc().isoformat(),
                        timeMax=end.utc().isoformat(),
                        timeZone=start.zoneName,
                        pageToken=pageToken,
                    )
                    .execute()
                )
                for event in response["items"]:
                    result.append(Event.fromApiDict(event))
                pageToken = response.get("nextPageToken")
                if not pageToken:
                    break
            return result

    # https://googleapis.github.io/google-api-python-client/docs/dyn/calendar_v3.events.html#insert
    def createEvent(self, event: Event) -> str:
        logger.info(
            "GoogleCalendarAPI: Adding Event %s starting at %s",
            event.title,
            str(event.start),
        )
        with googleapiclient.discovery.build(
            Constants.CALENDAR_SEVRVICE,
            Constants.CALENDAR_SERVICE_VERSION,
            credentials=self.delegatedCreds,
        ) as service:
            body = event.toApiDict()
            response = (
                service.events()
                .insert(calendarId=self.config.calendarId, body=body)
                .execute()
            )
            return Event.fromApiDict(response).link

    # def getCalendars(self):
    #     with googleapiclient.discovery.build(Constants.CALENDAR_SEVRVICE, Constants.CALENDAR_SERVICE_VERSION, credentials=self.delegatedCreds) as service:
    #         result = []
    #         page_token = None
    #         while True:
    #             calendar_list = service.calendarList().list(pageToken=page_token).execute()
    #             for calendar_list_entry in calendar_list['items']:
    #                 result.append(calendar_list_entry)
    #             page_token = calendar_list.get('nextPageToken')
    #             if not page_token:
    #                 break
    #         return result
