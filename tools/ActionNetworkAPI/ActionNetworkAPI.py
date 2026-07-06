"""Live Action Network OSDI client - the read path used to validate a signer as
a Member in Good Standing (MIG).

Lifted from the unmerged ``garrigan/voting`` branch
(``tools/ActionNetworkAPI/ActionNetoworkAPI.py``) and ported to Echo conventions
on the way in:

  * ``requests`` -> stdlib ``urllib`` (``requests`` is not an Echo dependency;
    the codebase uses ``urllib``/stdlib for HTTP, e.g. ``WikiAutomation/OutlineAPI.py``).
  * Fixed a latent bug: the original called ``datetime.date.strptime(...)``,
    which does not exist. Dates are parsed with ``datetime.datetime.strptime(...).date()``.
  * Dropped the unused write path (``postPeople`` / signup-helper); sign-on only
    reads a person record.

Deliberately framework-free (no Django imports) so it stays unit-testable and
mirrors the isolated-client house pattern. It is wrapped by the
``ANMIGValidator`` protocol (see ``migValidator.py``) - callers should depend on
that, not on this client directly, because ``__init__`` performs a live network
call (endpoint discovery) and so cannot be constructed in tests/demo.
"""
import dataclasses
import datetime
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from enum import Enum

logger = logging.getLogger(__name__)


class Constants:
    DATE_FORMAT = "%Y-%m-%d"

    API_ENTRY = "https://actionnetwork.org/api/v2/"

    # Person / response keys
    CUSTOM_FIELDS = "custom_fields"
    EMBEDDED = "_embedded"
    API_PEOPLE_KEY = "osdi:people"
    API_ENDPOINT = "href"
    API_ENDPOINTS_LIST = "_links"

    HEADER_API_KEY = "OSDI-API-Token"

    class CustomFieldKeys:
        MIGS_STATUS = "actionkit_is_member_in_good_standing"
        CHAPTER = "actionkit_user_chapter"
        JOIN_DATE = "actionkit_user_join_date"
        EXPIRE_DATE = "actionkit_user_xdate"

    # Action Network rate-limits at 4 req/s; stay under it.
    RATE_LIMIT_SECONDS = 0.35
    REQUEST_TIMEOUT_SECONDS = 30


@dataclasses.dataclass
class PersonInfoForVoteValidation:
    chapter: str
    memberStatus: bool
    expireDate: datetime.date
    joinDate: datetime.date


class GetPersonAPIReturnStatus(Enum):
    SUCCESS = 0
    INVALID_API_RESPONSE = 1
    NOT_FOUND = 2
    MULTIPLE_RECORDS_RETURNED = 3
    MISSING_REQUIRED_CUSTOM_FIELDS = 4


class InvalidAPIResponse(Exception):
    pass


class ActionNetworkAPI:
    """Thin OSDI client. Discovers the ``osdi:people`` endpoint on construction,
    then looks a person up by email for MIG validation."""

    def __init__(self, apiKey: str) -> None:
        self.apiKey = apiKey
        self.getPersonEndpoint = None
        self._initializeEndpoints()

    # --- transport -------------------------------------------------------

    def _headers(self) -> dict:
        return {Constants.HEADER_API_KEY: self.apiKey}

    def _get(self, url: str) -> dict:
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=Constants.REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            raise InvalidAPIResponse(f"Action Network returned HTTP {err.code} for {url}") from err
        except urllib.error.URLError as err:
            raise InvalidAPIResponse(f"Could not reach Action Network ({err.reason})") from err
        try:
            return json.loads(body)
        except json.JSONDecodeError as err:
            raise InvalidAPIResponse("Action Network response was not valid JSON") from err

    def _initializeEndpoints(self) -> None:
        responseDict = self._get(Constants.API_ENTRY)
        endpoints = responseDict.get(Constants.API_ENDPOINTS_LIST)
        if not isinstance(endpoints, dict):
            raise InvalidAPIResponse(
                f"Endpoints list ({Constants.API_ENDPOINTS_LIST}) was not a dictionary: {endpoints}"
            )
        peopleEndpoint = endpoints.get(Constants.API_PEOPLE_KEY)
        if not isinstance(peopleEndpoint, dict) or Constants.API_ENDPOINT not in peopleEndpoint:
            raise InvalidAPIResponse(
                f"People endpoint ({Constants.API_PEOPLE_KEY}) missing from API root"
            )
        self.getPersonEndpoint = peopleEndpoint[Constants.API_ENDPOINT]

    # --- the one read we need -------------------------------------------

    def getPersonForVoteValidation(
        self, email: str
    ) -> tuple[GetPersonAPIReturnStatus, PersonInfoForVoteValidation | None]:
        """Look a person up by email and return their MIG-relevant fields.

        Mirrors the voting branch contract exactly so its validation logic ports
        unchanged. Rate-limited per call (sign-on is one lookup, but keep the
        guard so any future batch use stays under the limit)."""
        logger.info("Looking up Action Network record for sign-on")
        query = urllib.parse.urlencode({"filter": f"email_address eq '{email}'"})
        responseDict = self._get(f"{self.getPersonEndpoint}?{query}")
        time.sleep(Constants.RATE_LIMIT_SECONDS)

        embedded = responseDict.get(Constants.EMBEDDED)
        if not isinstance(embedded, dict):
            logger.error("%s key not in person response", Constants.EMBEDDED)
            return (GetPersonAPIReturnStatus.INVALID_API_RESPONSE, None)

        personList = embedded.get(Constants.API_PEOPLE_KEY)
        if personList is None:
            logger.error("%s key not in embedded dict", Constants.API_PEOPLE_KEY)
            return (GetPersonAPIReturnStatus.INVALID_API_RESPONSE, None)
        if len(personList) == 0:
            return (GetPersonAPIReturnStatus.NOT_FOUND, None)
        if len(personList) > 1:
            return (GetPersonAPIReturnStatus.MULTIPLE_RECORDS_RETURNED, None)

        customFields = personList[0].get(Constants.CUSTOM_FIELDS)
        if not isinstance(customFields, dict):
            return (GetPersonAPIReturnStatus.INVALID_API_RESPONSE, None)

        required = (
            Constants.CustomFieldKeys.CHAPTER,
            Constants.CustomFieldKeys.MIGS_STATUS,
            Constants.CustomFieldKeys.EXPIRE_DATE,
            Constants.CustomFieldKeys.JOIN_DATE,
        )
        for key in required:
            if key not in customFields:
                logger.error("Required custom field %s missing", key)
                return (GetPersonAPIReturnStatus.MISSING_REQUIRED_CUSTOM_FIELDS, None)

        chapter = customFields[Constants.CustomFieldKeys.CHAPTER]
        memberStatus = str(customFields[Constants.CustomFieldKeys.MIGS_STATUS]).lower() in (
            "true", "yes", "1", "t",
        )
        try:
            expireDate = datetime.datetime.strptime(
                customFields[Constants.CustomFieldKeys.EXPIRE_DATE], Constants.DATE_FORMAT
            ).date()
            joinDate = datetime.datetime.strptime(
                customFields[Constants.CustomFieldKeys.JOIN_DATE], Constants.DATE_FORMAT
            ).date()
        except (ValueError, TypeError):
            logger.error("Could not parse join/expire date custom fields")
            return (GetPersonAPIReturnStatus.MISSING_REQUIRED_CUSTOM_FIELDS, None)

        return (
            GetPersonAPIReturnStatus.SUCCESS,
            PersonInfoForVoteValidation(
                chapter=chapter,
                memberStatus=memberStatus,
                expireDate=expireDate,
                joinDate=joinDate,
            ),
        )
