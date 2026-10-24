#!/usr/bin/env python3
"""
One-click Google Drive OAuth 2.0 Token Generator.
Uses Google Cloud SDK's built-in application client credentials.
Zero Google Cloud Console configuration needed!

Simply run:
    python tools/get_refresh_token.py
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

# Google Cloud SDK's official Client ID & Secret
GCLOUD_CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
GCLOUD_CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def main():
    print("=" * 65)
    print("  Google Cloud SDK (CLI) One-Click OAuth Generator")
    print("=" * 65)
    print("Using official Google Cloud SDK client credentials.")
    print("No manual Google Cloud Console setup required!\n")
    print("Opening your browser for Google sign-in...")
    print("Select your Google account and click 'Allow' to grant Drive access.\n")

    client_config = {
        "installed": {
            "client_id": GCLOUD_CLIENT_ID,
            "client_secret": GCLOUD_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"],
        }
    }

    try:
        flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
        creds = flow.run_local_server(
            port=8080,
            prompt="consent",
            access_type="offline",
            success_message="Authentication successful! You can close this window and return to your terminal.",
        )
    except Exception as e:
        print(f"\nAuthentication error: {e}")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("  SUCCESS! Copy these values to GitHub Secrets:")
    print("  (Repository Settings -> Secrets and variables -> Actions)")
    print("=" * 65)
    print(f"\n1. Secret: GDRIVE_CLIENT_ID\n{GCLOUD_CLIENT_ID}")
    print(f"\n2. Secret: GDRIVE_CLIENT_SECRET\n{GCLOUD_CLIENT_SECRET}")
    print(f"\n3. Secret: GDRIVE_REFRESH_TOKEN\n{creds.refresh_token}")
    print("=" * 65)


if __name__ == "__main__":
    main()
