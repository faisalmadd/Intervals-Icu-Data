#!/usr/bin/env python3
"""
One-time setup helper to generate a Google Drive OAuth 2.0 Refresh Token.
Runs locally on your machine, opens your browser to sign in, and outputs
the 3 secrets needed for GitHub Actions:
- GDRIVE_CLIENT_ID
- GDRIVE_CLIENT_SECRET
- GDRIVE_REFRESH_TOKEN
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def main():
    print("=" * 60)
    print("  Google Drive OAuth 2.0 Token Generator")
    print("=" * 60)
    print("Enter the Client ID and Client Secret from your Google Cloud Console:")
    print("(GCP Console -> APIs & Services -> Credentials -> OAuth 2.0 Client IDs)\n")

    client_id = input("Client ID: ").strip()
    if not client_id:
        print("Error: Client ID cannot be empty.")
        sys.exit(1)

    client_secret = input("Client Secret: ").strip()
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

    print("\nOpening your web browser for Google sign-in...")
    print("If you see an 'unverified app' warning, click 'Advanced' -> 'Go to Intervals Sync (unsafe)'.")
    print("Grant Drive access, and you'll be redirected back here.\n")

    try:
        flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
        creds = flow.run_local_server(
            port=8080,
            prompt="consent",
            access_type="offline",
            success_message="Authentication successful! You can close this window and return to the terminal.",
        )
    except Exception as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  SUCCESS! Copy these 3 values to GitHub Secrets:")
    print("  (Settings -> Secrets and variables -> Actions)")
    print("=" * 60)
    print(f"\n1. Secret Name: GDRIVE_CLIENT_ID\nValue:\n{client_id}")
    print(f"\n2. Secret Name: GDRIVE_CLIENT_SECRET\nValue:\n{client_secret}")
    print(f"\n3. Secret Name: GDRIVE_REFRESH_TOKEN\nValue:\n{creds.refresh_token}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
