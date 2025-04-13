
from ..EventAutomation.ZoomAPI import ZoomConfig
from ..EventAutomation.ActionNetworkAutomation import ANAutomatorConfig
from ..EventAutomation.GoogleCalendarAPI import GoogleCalendarConfig

# TODO: Actually have this store/retrieve secrets
from .devSecrets import *

def getZoomConfig() -> ZoomConfig:
    return ZoomConfig(accountId=ZoomAccountId(), clientId=ZoomClientId(), clientSecret=ZoomClientSecret())

def getANAutomatorConfig() -> ANAutomatorConfig:
    return ANAutomatorConfig(email=ANUserName(), password=ANPassword())

def getGCalConfig() -> GoogleCalendarConfig:
    return GoogleCalendarConfig(serviceKeyPath=GoogleServiceKeyPath(), calendarId=GoogleCalId(),delegateAccount=GoogleDelegateAccount())

def getWebsiteEmailAccountUserName() -> str:
    return WebsiteEmailAccountUsername()

def getWebsiteEmailAccountPassword() -> str:
    return WebsiteEmailAccountPassword()