from django.test import override_settings

# PBKDF2 (Django default) is the dominant cost in create_user. MD5 is ~100x
# faster and adequate for tests that only need a *storable, force-login-able*
# password - NOT for tests that assert real password strength/verification.
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

fastHashing = override_settings(PASSWORD_HASHERS=FAST_HASHERS)


class FastPasswordHasherMixin:
    """Class decorator-equivalent as a mixin so it composes with the others.
    Apply by decorating the class with @fastHashing, or inherit this mixin which
    wires override_settings in setUpClass/tearDownClass."""
    @classmethod
    def setUpClass(cls):
        cls._fastHashing = override_settings(PASSWORD_HASHERS=FAST_HASHERS)
        cls._fastHashing.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._fastHashing.disable()
