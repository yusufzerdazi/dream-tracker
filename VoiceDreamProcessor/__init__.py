import io
import json
import logging
import os

import azure.functions as func
import gkeepapi
import msal
import requests
from azure.storage.blob import BlobServiceClient
from openai import OpenAI

# Environment variables
GOOGLE_EMAIL = os.getenv('GoogleEmail')
GOOGLE_PASSWORD = os.getenv('GooglePassword')
STORAGE = os.getenv('StorageAccountConnectionString')
MS_GRAPH_CLIENT_ID = os.getenv('MS_GRAPH_CLIENT_ID')
MS_GRAPH_TENANT_ID = os.getenv('MS_GRAPH_TENANT_ID', 'consumers')

GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'
ONEDRIVE_FOLDER_PATH = '/me/drive/root:/Recordings/Interview%20Recordings:/children'
AUDIO_MIME_TYPES = {
    'audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/wav',
    'audio/webm', 'audio/ogg', 'audio/flac', 'audio/amr',
    'audio/3gpp', 'audio/3gpp2',
}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

TOKEN_CACHE_BLOB = 'voice-processing/msal_token_cache.json'
PROCESSED_FILES_BLOB = 'voice-processing/processed_files.json'


def get_msal_app(container_client):
    """Create an MSAL PublicClientApplication with token cache from blob storage."""
    cache = msal.SerializableTokenCache()

    # Load existing cache from blob
    try:
        blob_client = container_client.get_blob_client(TOKEN_CACHE_BLOB)
        cache_data = blob_client.download_blob().readall().decode('utf-8')
        cache.deserialize(cache_data)
        logging.info("Loaded MSAL token cache from blob storage.")
    except Exception as e:
        logging.warning(f"Could not load MSAL token cache: {e}")

    authority = f'https://login.microsoftonline.com/{MS_GRAPH_TENANT_ID}'
    app = msal.PublicClientApplication(
        MS_GRAPH_CLIENT_ID,
        authority=authority,
        token_cache=cache,
    )
    return app, cache


def save_token_cache(cache, container_client):
    """Save the MSAL token cache back to blob storage if it changed."""
    if cache.has_state_changed:
        blob_client = container_client.get_blob_client(TOKEN_CACHE_BLOB)
        blob_client.upload_blob(cache.serialize(), overwrite=True)
        logging.info("Saved updated MSAL token cache to blob storage.")


def acquire_token(app):
    """Acquire a Microsoft Graph access token using silent auth."""
    accounts = app.get_accounts()
    if not accounts:
        logging.error("No accounts found in MSAL token cache. Run auth_setup.py first.")
        return None

    result = app.acquire_token_silent(
        scopes=['https://graph.microsoft.com/Files.Read'],
        account=accounts[0],
    )

    if not result:
        logging.error("acquire_token_silent returned None. Token may have expired. Re-run auth_setup.py.")
        return None

    if 'error' in result:
        logging.error(f"Token acquisition error: {result['error']} - {result.get('error_description', '')}")
        return None

    return result['access_token']


def list_onedrive_files(access_token):
    """List audio files from the OneDrive recordings folder."""
    url = f'{GRAPH_BASE_URL}{ONEDRIVE_FOLDER_PATH}'
    headers = {'Authorization': f'Bearer {access_token}'}

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    items = response.json().get('value', [])
    audio_files = []
    for item in items:
        mime_type = item.get('file', {}).get('mimeType', '')
        if mime_type in AUDIO_MIME_TYPES:
            audio_files.append(item)

    logging.info(f"Found {len(audio_files)} audio files in OneDrive.")
    return audio_files


def load_processed_files(container_client):
    """Load the set of already-processed file IDs from blob storage."""
    try:
        blob_client = container_client.get_blob_client(PROCESSED_FILES_BLOB)
        data = blob_client.download_blob().readall().decode('utf-8')
        return set(json.loads(data))
    except Exception:
        return set()


def save_processed_files(processed_ids, container_client):
    """Save the set of processed file IDs to blob storage."""
    blob_client = container_client.get_blob_client(PROCESSED_FILES_BLOB)
    blob_client.upload_blob(json.dumps(list(processed_ids)), overwrite=True)


def download_audio(item):
    """Download an audio file using the pre-authenticated download URL."""
    download_url = item.get('@microsoft.graph.downloadUrl')
    if not download_url:
        logging.error(f"No download URL for file: {item['name']}")
        return None

    response = requests.get(download_url)
    response.raise_for_status()
    return response.content


def transcribe_audio(audio_data, filename, openai_client):
    """Transcribe audio using OpenAI's gpt-4o-mini-transcribe model."""
    audio_file = io.BytesIO(audio_data)
    audio_file.name = filename

    transcript = openai_client.audio.transcriptions.create(
        model='gpt-4o-mini-transcribe',
        file=audio_file,
    )
    return transcript.text


def create_keep_note(keep, title, text):
    """Create a Google Keep note with the Dream label."""
    note = keep.createNote(title=title, text=text)

    # Find or create the Dream label
    dream_label = keep.findLabel('Dream')
    if not dream_label:
        dream_label = keep.createLabel('Dream')

    note.labels.add(dream_label)
    keep.sync()
    logging.info(f"Created Keep note: '{title}'")
    return note


def main(mytimer: func.TimerRequest) -> None:
    logging.info("VoiceDreamProcessor triggered.")

    if not MS_GRAPH_CLIENT_ID:
        logging.error("MS_GRAPH_CLIENT_ID not configured. Skipping.")
        return

    # Initialize clients
    blob_service_client = BlobServiceClient.from_connection_string(STORAGE)
    container_client = blob_service_client.get_container_client("dreams")
    openai_client = OpenAI()

    # Authenticate with Microsoft Graph
    app, cache = get_msal_app(container_client)
    access_token = acquire_token(app)
    if not access_token:
        return

    # Always save cache in case tokens were refreshed
    save_token_cache(cache, container_client)

    # List audio files from OneDrive
    try:
        audio_files = list_onedrive_files(access_token)
    except requests.exceptions.HTTPError as e:
        logging.error(f"Failed to list OneDrive files: {e}")
        return

    if not audio_files:
        logging.info("No audio files found. Done.")
        return

    # Load already-processed file IDs
    processed_ids = load_processed_files(container_client)
    logging.info(f"{len(processed_ids)} files already processed.")

    # Filter to unprocessed files
    new_files = [f for f in audio_files if f['id'] not in processed_ids]
    logging.info(f"{len(new_files)} new audio files to process.")

    if not new_files:
        logging.info("No new files to process. Done.")
        return

    # Authenticate with Google Keep (only if we have work to do)
    keep = gkeepapi.Keep()
    try:
        keep.authenticate(GOOGLE_EMAIL, GOOGLE_PASSWORD)
    except Exception as e:
        logging.error(f"Failed to authenticate with Google Keep: {e}")
        return

    for item in new_files:
        file_id = item['id']
        filename = item['name']
        file_size = item.get('size', 0)

        # Skip oversized files (mark as processed to avoid retries)
        if file_size > MAX_FILE_SIZE:
            logging.warning(f"Skipping oversized file ({file_size} bytes): {filename}")
            processed_ids.add(file_id)
            save_processed_files(processed_ids, container_client)
            continue

        logging.info(f"Processing: {filename} ({file_size} bytes)")

        # Download
        try:
            audio_data = download_audio(item)
            if not audio_data:
                continue
        except Exception as e:
            logging.error(f"Failed to download {filename}: {e}")
            continue  # Transient — leave unprocessed for retry

        # Transcribe
        try:
            transcript = transcribe_audio(audio_data, filename, openai_client)
            logging.info(f"Transcription for {filename}: {transcript[:100]}...")
        except Exception as e:
            logging.error(f"Failed to transcribe {filename}: {e}")
            continue  # Transient — leave unprocessed for retry

        # Create Keep note
        try:
            title = os.path.splitext(filename)[0]
            create_keep_note(keep, title, transcript)
        except Exception as e:
            logging.error(f"Failed to create Keep note for {filename}: {e}")
            continue  # Transient — leave unprocessed for retry

        # Mark as processed
        processed_ids.add(file_id)
        save_processed_files(processed_ids, container_client)
        logging.info(f"Successfully processed: {filename}")

    logging.info("VoiceDreamProcessor completed.")
