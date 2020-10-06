import datetime
import logging
import os
import json

import azure.functions as func
import gkeepapi
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient

GOOGLE_EMAIL = os.getenv('GoogleEmail')
GOOGLE_PASSWORD = os.getenv('GooglePassword')
STORAGE = os.getenv('StorageAccountConnectionString')
COG_ENDPOINT = os.getenv('CognitiveServicesEndpoint')
COG_KEY = os.getenv('CognitiveServicesKey')


def setup():
    global keep, text_analytics_client, container_client

    keep = gkeepapi.Keep()
    keep.login(GOOGLE_EMAIL, GOOGLE_PASSWORD)
    ta_credential = AzureKeyCredential(COG_KEY)
    text_analytics_client = TextAnalyticsClient(
            endpoint=COG_ENDPOINT, 
            credential=ta_credential)
    blob_service_client = BlobServiceClient.from_connection_string(STORAGE)
    container_client = blob_service_client.get_container_client("dreams")

def parse_dream(keep_dream):
    return {
        "id": keep_dream.id,
        "title": keep_dream.title,
        "labels": [label.name for label in keep_dream.labels.all()],
        "timestamp": str(keep_dream.timestamps.created),
        "text": keep_dream.text
    }

def parse_sentiment(sentiment):
    return {
        "sentiment": sentiment.sentiment,
        "confidence_scores": {
            "positive": sentiment.confidence_scores.positive,
            "neutral": sentiment.confidence_scores.neutral,
            "negative": sentiment.confidence_scores.negative,
        },
        "sentences": [{
            "text": sentence.text,
            "sentiment": sentence.sentiment,
            "confidence_scores": {
                "positive": sentence.confidence_scores.positive,
                "neutral": sentence.confidence_scores.neutral,
                "negative": sentence.confidence_scores.negative,
            }
        } for sentence in sentiment.sentences]
    }

def parse_key_phrases(key_phrases):
    return key_phrases.key_phrases

def parse_entities(entities):
    logging.info(entities)
    return [{"text": entity.text, "category": entity.category, "subcategory": entity.subcategory, "confidence_score": entity.confidence_score} for entity in entities.entities]

def batch(iterable, n=1):
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx:min(ndx + n, l)]

def main(mytimer: func.TimerRequest) -> None:
    global keep, text_analytics_client, container_client

    logging.info("Function triggered.")
    setup()

    logging.info("Fetching existing dreams.")
    existing_dream_ids = [os.path.splitext(blob.name)[0] for blob in container_client.list_blobs()]

    logging.info("Fetching new dreams.")
    dreams = keep.find(labels=[keep.findLabel("Dream")])

    new_dreams = [dream for dream in dreams if dream.id not in existing_dream_ids]
    logging.info(str(len(new_dreams)) + " new dreams found.")

    cog_dreams = batch([{"id": dream.id, "text": dream.text} for dream in new_dreams], 5)
    cog_result_sentiment = []
    cog_result_key_phrases = []
    cog_result_entities = []

    logging.info("Analysing dreams.")
    for cog_dream_batch in cog_dreams:
        cog_result_sentiment.extend(text_analytics_client.analyze_sentiment(cog_dream_batch))
        cog_result_key_phrases.extend(text_analytics_client.extract_key_phrases(cog_dream_batch))
        cog_result_entities.extend(text_analytics_client.recognize_entities(cog_dream_batch))

    logging.info("Uploading dreams.")
    for dream in new_dreams:
        parsed_dream = parse_dream(dream)
        sentiment = parse_sentiment([d for d in cog_result_sentiment if d.id == dream.id][0])
        key_phrases = parse_key_phrases([d for d in cog_result_key_phrases if d.id == dream.id][0])
        entities = parse_entities([d for d in cog_result_entities if d.id == dream.id][0])

        parsed_dream["sentiment"] = sentiment
        parsed_dream["key_phrases"] = key_phrases,
        parsed_dream["entities"] = entities

        blob_client = container_client.get_blob_client(dream.id + ".json")
        blob_client.upload_blob(json.dumps(parsed_dream))
