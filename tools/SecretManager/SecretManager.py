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


def getMembershipBotEmailConfig() -> tuple[str, str] | None:
    """Credentials for the austindsalistbot Gmail inbox (the membership-list
    ingest's source of national's monthly rosters).

    Returns (username, password) or None when unconfigured, so
    ingest_membership_lists can boot and take its --from-dir path (or warn
    and exit 0 for the live-email path) before Garrigan fills these in. The
    password must be a Gmail app password, never the raw account password.
    """
    username = MembershipBotEmailUsername()
    password = MembershipBotEmailPassword()
    if not username or not password:
        return None
    return (username, password)
