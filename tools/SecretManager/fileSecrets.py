import os
import json
import logging

logger = logging.getLogger(__name__)

class Keys:
    ZOOM_ACCOUNT_ID = "ZoomAccountId"
    ZOOM_CLIENT_ID = "ZoomClientId"
    ZOOM_CLIENT_SECRET = "ZoomClientSecret"
    AN_USERNAME = "AnUsername"
    AN_PASSWORD = "AnPassword"
    GOOGLE_SERVICE_KEY_PATH = "GoogleServiceKeyPath"
    GOOGLE_CAL_ID = "GoogleCalId"
    GOOGLE_DELEGATE_ACCOUNT = "GoogleDelegateAccount"
    WEBSITE_EMAIL_ACCOUNT_USERNAME = "WebsiteEmailAccountUsername"
    WEBSITE_EMAIL_ACCOUNT_PASSWORD = "WebsiteEmailAccountPassword"

def _readSecretsFromFile():
    logger.info("Loading Secrets from File")
    secretsFile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.json")
    secretObject = {}
    with open(secretsFile) as f:
        secretObject = json.load(secretsFile)
    logger.info("Validating object")
    for name, value in vars(Keys).items():
        if not name.startswith("__") and not callable(value):
            if value not in secretObject:
                logger.error("Key %s does not exist in secret file", value)
                raise Exception(f"Key {value} does not exist in secret file")
    return secretObject


secretObject = _readSecretsFromFile()

def ZoomAccountId():
    return secretObject[Keys.ZOOM_ACCOUNT_ID]


def ZoomClientId():
    return secretObject[Keys.ZOOM_CLIENT_ID]


def ZoomClientSecret():
    return secretObject[Keys.ZOOM_CLIENT_SECRET]


def ANUserName():
    return secretObject[Keys.AN_USERNAME]


def ANPassword():
    return secretObject[Keys.AN_PASSWORD]


def GoogleServiceKeyPath():
    return secretObject[Keys.GOOGLE_SERVICE_KEY_PATH]


def GoogleCalId():
    return secretObject[Keys.GOOGLE_CAL_ID]


def GoogleDelegateAccount():
    return secretObject[Keys.GOOGLE_DELEGATE_ACCOUNT]


def WebsiteEmailAccountUsername():
    return secretObject[Keys.WEBSITE_EMAIL_ACCOUNT_USERNAME]


def WebsiteEmailAccountPassword():
    return secretObject[Keys.WEBSITE_EMAIL_ACCOUNT_PASSWORD]

