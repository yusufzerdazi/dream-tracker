"""
One-time setup script for Microsoft Graph authentication.

This script uses MSAL device code flow to interactively authenticate
with a personal Microsoft account, then saves the token cache
(containing the refresh token) to Azure Blob Storage.

Usage:
    1. Set MS_GRAPH_CLIENT_ID in local.settings.json or as an environment variable
    2. Set StorageAccountConnectionString in local.settings.json or as an environment variable
    3. Run: python auth_setup.py
    4. Follow the device code instructions in the browser
    5. The token cache is saved to blob storage for use by VoiceDreamProcessor

Re-run this script if tokens expire (90 days of inactivity).
"""

import json
import os
import sys

import msal
from azure.storage.blob import BlobServiceClient

# Try to load settings from local.settings.json
settings = {}
try:
    with open('local.settings.json', 'r') as f:
        settings = json.load(f).get('Values', {})
except FileNotFoundError:
    pass

CLIENT_ID = os.getenv('MS_GRAPH_CLIENT_ID') or settings.get('MS_GRAPH_CLIENT_ID')
TENANT_ID = os.getenv('MS_GRAPH_TENANT_ID') or settings.get('MS_GRAPH_TENANT_ID', 'consumers')
STORAGE_CONN = os.getenv('StorageAccountConnectionString') or settings.get('StorageAccountConnectionString')

TOKEN_CACHE_BLOB = 'voice-processing/msal_token_cache.json'
SCOPES = ['https://graph.microsoft.com/Files.Read']


def main():
    if not CLIENT_ID:
        print("ERROR: MS_GRAPH_CLIENT_ID is not set.")
        print("Set it in local.settings.json or as an environment variable.")
        sys.exit(1)

    if not STORAGE_CONN:
        print("ERROR: StorageAccountConnectionString is not set.")
        print("Set it in local.settings.json or as an environment variable.")
        sys.exit(1)

    authority = f'https://login.microsoftonline.com/{TENANT_ID}'
    app = msal.PublicClientApplication(CLIENT_ID, authority=authority)

    # Initiate device code flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    if 'user_code' not in flow:
        print(f"ERROR: Failed to create device flow: {json.dumps(flow, indent=2)}")
        sys.exit(1)

    print(flow['message'])
    print()

    # Wait for user to complete authentication
    result = app.acquire_token_by_device_flow(flow)

    if 'access_token' in result:
        print("Authentication successful!")
        print(f"Authenticated as: {result.get('id_token_claims', {}).get('preferred_username', 'unknown')}")

        # Save token cache to blob storage
        blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONN)
        container_client = blob_service_client.get_container_client("dreams")
        blob_client = container_client.get_blob_client(TOKEN_CACHE_BLOB)

        cache_data = app.token_cache.serialize()
        blob_client.upload_blob(cache_data, overwrite=True)
        print(f"Token cache saved to blob storage: {TOKEN_CACHE_BLOB}")
        print("VoiceDreamProcessor is now ready to use.")
    else:
        print(f"ERROR: Authentication failed: {result.get('error')}")
        print(f"Description: {result.get('error_description')}")
        sys.exit(1)


if __name__ == '__main__':
    main()
