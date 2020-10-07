import logging
import os
import json

import azure.functions as func
from azure.storage.blob import BlobServiceClient

STORAGE = os.getenv('StorageAccountConnectionString')

def setup():
    global summary_container_client, dream_container_client
    blob_service_client = BlobServiceClient.from_connection_string(STORAGE)
    summary_container_client = blob_service_client.get_container_client("summary")
    dream_container_client = blob_service_client.get_container_client("dreams")

def main(mytimer: func.TimerRequest) -> None:
    global summary_container_client, dream_container_client
    setup()

    existing_dream_ids = [os.path.splitext(blob.name)[0] for blob in dream_container_client.list_blobs()]
    existing_summary = json.loads(summary_container_client.download_blob("dreams.json").content_as_text())

    new_dream_ids = [dream_id for dream_id in existing_dream_ids if dream_id not in [dream["id"] for dream in existing_summary]]
    logging.info(new_dream_ids)

    for dream_id in new_dream_ids:
        dream_text = dream_container_client.download_blob(dream_id + ".json")
        dream = json.loads(dream_text.content_as_text())
        existing_summary.append({
            "id": dream["id"],
            "key_phrases": dream["key_phrases"][0],
            "sentiment": {
                "sentiment": dream["sentiment"]["sentiment"],
                "confidence_scores": dream["sentiment"]["confidence_scores"]
            },
            "timestamp": dream["timestamp"],
            "labels": dream["labels"]
        })

    blob_client = summary_container_client.get_blob_client("dreams.json")
    blob_client.upload_blob(json.dumps(existing_summary), overwrite=True)
