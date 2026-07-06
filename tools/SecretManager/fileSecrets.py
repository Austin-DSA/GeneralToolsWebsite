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
    # Not needed right now, assume the service key is in this directory
    # GOOGLE_SERVICE_KEY_PATH = "GoogleServiceKeyPath"
    GOOGLE_CAL_ID = "GoogleCalId"
    GOOGLE_DELEGATE_ACCOUNT = "GoogleDelegateAccount"
    WEBSITE_EMAIL_ACCOUNT_USERNAME = "WebsiteEmailAccountUsername"
    WEBSITE_EMAIL_ACCOUNT_PASSWORD = "WebsiteEmailAccountPassword"
    # Outline wiki — Link Tree read-only service account. OPTIONAL (see
    # OPTIONAL_KEYS): when absent, wiki-backed link items simply stay unresolved
    # and hidden, so the app still boots without these configured.
    OUTLINE_BASE_URL = "OutlineBaseUrl"
    OUTLINE_READ_API_TOKEN = "OutlineReadApiToken"
    # Action Network OSDI API token for the live MIG check (resolution sign-on).
    # OPTIONAL (see OPTIONAL_KEYS): absent on the demo box, where sign-on falls
    # back to the mock validator. Distinct from AN_USERNAME/AN_PASSWORD (the
    # Selenium login event automation uses).
    AN_API_KEY = "ANAPIKey"

# Keys that are not required at import. The accessors below return None when an
# optional key is missing; callers must handle the unconfigured case.
OPTIONAL_KEYS = frozenset({
    Keys.OUTLINE_BASE_URL,
    Keys.OUTLINE_READ_API_TOKEN,
    Keys.AN_API_KEY,
})

def _readSecretsFromFile():
    logger.info("Loading Secrets from File")
    secretsFile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.json")
    secretObject = {}
    with open(secretsFile) as f:
        secretObject = json.load(f)
    logger.info("Validating object")
    for name, value in vars(Keys).items():
        if not name.startswith("__") and not callable(value):
            if value in OPTIONAL_KEYS:
                continue
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
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "serviceKey.json")


def GoogleCalId():
    return secretObject[Keys.GOOGLE_CAL_ID]


def GoogleDelegateAccount():
    return secretObject[Keys.GOOGLE_DELEGATE_ACCOUNT]


def WebsiteEmailAccountUsername():
    return secretObject[Keys.WEBSITE_EMAIL_ACCOUNT_USERNAME]


def WebsiteEmailAccountPassword():
    return secretObject[Keys.WEBSITE_EMAIL_ACCOUNT_PASSWORD]


def OutlineBaseUrl():
    # Optional — None when not configured (see OPTIONAL_KEYS).
    return secretObject.get(Keys.OUTLINE_BASE_URL)


def OutlineReadApiToken():
    # Optional — None when not configured (see OPTIONAL_KEYS).
    return secretObject.get(Keys.OUTLINE_READ_API_TOKEN)


def ANAPIKey():
    # Optional — None when not configured (see OPTIONAL_KEYS). Absent on the
    # demo box; sign-on falls back to the mock validator.
    return secretObject.get(Keys.AN_API_KEY)

