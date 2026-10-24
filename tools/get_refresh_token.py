#!/usr/bin/env python3
"""
Google Drive OAuth 2.0 Token Generator.
Generates a Refresh Token using your own project's Desktop OAuth Client.

Usage:
    python tools/get_refresh_token.py
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

# Using drive.file (non-restricted scope, never blocked by Google)
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]


def main():
    print("=" * 65)
    print("  Google Drive OAuth 2.0 Token Generator")
    print("=" * 65)
    print("Google blocks third-party apps from accessing Drive full-scope.")
    print("Using your own project's Desktop Client ID avoids this block completely.\n")

    client_id = input("Enter your Client ID: ").strip()
    if not client_id:
        print("Error: Client ID cannot be empty.")
        sys.exit(1)

    client_secret = input("Enter your Client Secret: ").strip()
    if not client_secret:
        print("Error: Client Secret cannot be empty.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"],
        }
    }

    print("\nOpening your browser for Google sign-in...")
    print("1. Select your Google account.")
    print("2. When you see 'Google hasn't verified this app', click 'Advanced'.")
    print("3. Click 'Go to Intervals Sync (unsafe)' and click 'Continue'.\n")

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
    print("  SUCCESS! Copy these 3 values to GitHub Secrets:")
    print("  (Settings -> Secrets and variables -> Actions)")
    print("=" * 65)
    print(f"\n1. Secret Name: GDRIVE_CLIENT_ID\n{client_id}")
    print(f"\n2. Secret Name: GDRIVE_CLIENT_SECRET\n{client_secret}")
    print(f"\n3. Secret Name: GDRIVE_REFRESH_TOKEN\n{creds.refresh_token}")
    print("=" * 65)


if __name__ == "__main__":
    main()
