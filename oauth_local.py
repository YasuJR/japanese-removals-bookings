"""Local Google OAuth bootstrap.

Import this module BEFORE any google_auth_oauthlib / oauthlib import.
Allows http://127.0.0.1 redirect URIs during development only.
"""

import os

import config

if not config.PRODUCTION:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
