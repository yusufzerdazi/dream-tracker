import datetime
import logging
import os
import json

import azure.functions as func
import gkeepapi
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient


# GOOGLE_EMAIL = os.getenv('GoogleEmail')
# GOOGLE_PASSWORD = os.getenv('GooglePassword')
# STORAGE = os.getenv('StorageAccountConnectionString')
# COG_ENDPOINT = os.getenv('CognitivServicesEndpoint')
# COG_KEY = os.getenv('CognitiveServicesKey')

keep = gkeepapi.Keep()
success = keep.login(GOOGLE_EMAIL, GOOGLE_PASSWORD)
ta_credential = AzureKeyCredential(COG_KEY)
text_analytics_client = TextAnalyticsClient(
        endpoint=COG_ENDPOINT, 
        credential=ta_credential)
blob_service_client = BlobServiceClient.from_connection_string(STORAGE)
# container_client = blob_service_client.get_container_client("dreams")

results = keep.find(labels=[keep.findLabel("Dream")])
note = next(results)

note_parsed = {
    "id": note.id,
    "title": note.title,
    "labels": [label.name for label in note.labels.all()],
    "timestamp": str(note.timestamps.created),
    "text": note.text
}

# cog_result_sentiment = text_analytics_client.analyze_sentiment([note_parsed["text"]])
cog_result_key_phrases = text_analytics_client.recognize_linked_entities([note_parsed["text"]])

# print(cog_result_sentiment)
print(cog_result_key_phrases)

def main(mytimer: func.TimerRequest) -> None:
    utc_timestamp = datetime.datetime.utcnow().replace(
        tzinfo=datetime.timezone.utc).isoformat()

    if mytimer.past_due:
        logging.info('The timer is past due!')

    logging.info('Python timer trigger function ran at %s', utc_timestamp)

    results = keep.find(labels=[keep.findLabel("Dream")])
    for result in results:
        logging.info(result.text)
