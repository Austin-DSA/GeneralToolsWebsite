import dataclasses

from ..EventAutomation.ZoomAPI import ZoomConfig
from ..EventAutomation.ActionNetworkAutomation import ANAutomatorConfig
from ..EventAutomation.GoogleCalendarAPI import GoogleCalendarConfig
from ..WikiAutomation.OutlineAPI import OutlineConfig

import settings

if settings.DEBUG:
    from .devSecrets import *
else:
    from .fileSecrets import *


def getZoomConfig() -> ZoomConfig:
    return ZoomConfig(
        accountId=ZoomAccountId(),
        clientId=ZoomClientId(),
        clientSecret=ZoomClientSecret(),
    )


def getANAutomatorConfig() -> ANAutomatorConfig:
    return ANAutomatorConfig(email=ANUserName(), password=ANPassword())


def getGCalConfig() -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        serviceKeyPath=GoogleServiceKeyPath(),
        calendarId=GoogleCalId(),
        delegateAccount=GoogleDelegateAccount(),
    )


def getWebsiteEmailAccountUserName() -> str:
    return WebsiteEmailAccountUsername()


def getWebsiteEmailAccountPassword() -> str:
    return WebsiteEmailAccountPassword()


def getOutlineReadConfig() -> OutlineConfig | None:
    """Outline client config for the Link Tree's wiki service account.

    This token needs the ``documents.search`` + ``documents.info`` scopes to
    surface published wiki content (e.g. GBM agendas), plus ``shares.create`` +
    ``shares.update`` so resolved items can link to a published share URL
    (readable without a wiki login). It is intended to live on a dedicated
    bot/service account, separate from any human editor's token.

    Returns ``None`` when the token isn't configured, so wiki surfacing degrades
    gracefully (items stay unresolved/hidden) instead of breaking the deploy or
    the sync command. Callers must handle ``None``.
    """
    baseUrl = OutlineBaseUrl()
    apiToken = OutlineReadApiToken()
    if not baseUrl or not apiToken:
        return None
    return OutlineConfig(baseUrl=baseUrl, apiToken=apiToken)


@dataclasses.dataclass
class LCNotesConfig:
    titlePattern: str
    fallbackEmail: str
    keywords: list[str]


def getOutlineConfig() -> OutlineConfig:
    return OutlineConfig(
        baseUrl=OutlineBaseUrl(),
        apiToken=OutlineApiToken(),
    )


def getLCNotesConfig() -> LCNotesConfig:
    # Imported lazily to keep this module's import graph shallow at Django
    # settings-load time (settings.py imports SecretManager).
    from ..WikiAutomation.LCNotePublisher import DEFAULT_KEYWORDS, DEFAULT_TITLE_PATTERN

    overrideKeywords = LCExecKeywords()
    overrideTitlePattern = LCNotesTitlePattern()
    return LCNotesConfig(
        titlePattern=overrideTitlePattern if overrideTitlePattern else DEFAULT_TITLE_PATTERN,
        fallbackEmail=LCNotesFallbackEmail(),
        keywords=overrideKeywords if overrideKeywords else DEFAULT_KEYWORDS,
    )
