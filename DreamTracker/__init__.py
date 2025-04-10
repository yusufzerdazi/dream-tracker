import datetime
import logging
import os
import json

import azure.functions as func
import gkeepapi
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from openai import OpenAI

# Proxy configuration - comment out if not needed
# proxy = 'http://pi.zerdazi.com:8118'

# os.environ['http_proxy'] = proxy
# os.environ['HTTP_PROXY'] = proxy
# os.environ['https_proxy'] = proxy
# os.environ['HTTPS_PROXY'] = proxy

# Environment variables
GOOGLE_EMAIL = os.getenv('GoogleEmail')
GOOGLE_PASSWORD = os.getenv('GooglePassword')
STORAGE = os.getenv('StorageAccountConnectionString')
COG_ENDPOINT = os.getenv('CognitiveServicesEndpoint')
COG_KEY = os.getenv('CognitiveServicesKey')


def setup():
    global keep, text_analytics_client, container_client, openai_client

    keep = gkeepapi.Keep()
    keep.authenticate(GOOGLE_EMAIL, GOOGLE_PASSWORD)
    ta_credential = AzureKeyCredential(COG_KEY)
    text_analytics_client = TextAnalyticsClient(
            endpoint=COG_ENDPOINT, 
            credential=ta_credential)
    blob_service_client = BlobServiceClient.from_connection_string(STORAGE)
    container_client = blob_service_client.get_container_client("dreams")
    openai_client = OpenAI()

def parse_dream(keep_dream):
    # Use edited time if available, otherwise fall back to created time
    last_modified = keep_dream.timestamps.edited if keep_dream.timestamps.edited else keep_dream.timestamps.created
    
    return {
        "id": keep_dream.id,
        "title": keep_dream.title,
        "labels": [label.name for label in keep_dream.labels.all()],
        "timestamp": str(keep_dream.timestamps.created),
        "lastModified": str(last_modified),
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

def generate_dream_metadata(dream_text):
    """Generate title and tags for a dream using a single OpenAI call."""
    try:
        # Load prompt template from environment variable with fallback to a basic template
        prompt_template = os.getenv('DREAM_ANALYZER_PROMPT')
        
        # Format the prompt with the dream text
        prompt = prompt_template.format(dream_text=dream_text)
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a dream analyzer that creates meaningful titles and tags for dreams."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_tokens=150
        )
        
        # Extract and parse the JSON response
        content = response.choices[0].message.content.strip()
        metadata = json.loads(content)
        
        # Validate the response
        if "title" not in metadata or "tags" not in metadata:
            logging.warning(f"Invalid metadata format received: {content}")
            return {"title": "Untitled Dream", "tags": []}
            
        if not isinstance(metadata["tags"], list):
            metadata["tags"] = []
            
        return metadata
    except Exception as e:
        logging.error(f"Error generating dream metadata: {str(e)}")
        return {"title": "Untitled Dream", "tags": []}

def update_keep_with_tags(dream, new_tags, ai_title):
    """Update Google Keep note with new tags, format title, and set color."""
    try:
        # Format the title with date prefix "DD/MM/YYYY: <dream title>"
        created_date = dream.timestamps.created
        date_prefix = created_date.strftime("%d/%m/%Y")
        
        # Use the provided AI title
        new_title = f"{date_prefix}: {ai_title}"
        dream.title = new_title
            
        # Set note color to yellow
        dream.color = gkeepapi.node.ColorValue.Yellow
        
        # Add new tags to the dream note
        for tag in new_tags:
            # Find or create label
            label = keep.findLabel(tag)
            if not label:
                label = keep.createLabel(tag)
            
            # Add label to note if it doesn't already have it
            if label not in dream.labels.all():
                dream.labels.add(label)
        
        # Sync changes back to Google Keep
        keep.sync()
        return True
    except Exception as e:
        logging.error(f"Error updating Keep note: {str(e)}")
        return False

def sync_dreams_from_keep_to_blob(all_dreams):
    """Synchronize dreams from Google Keep to Blob storage based on LastModified timestamps."""
    logging.info("Starting synchronization from Keep to Blob storage.")
    
    # Get all blobs from storage
    blobs = list(container_client.list_blobs())
    blob_dict = {os.path.splitext(blob.name)[0]: blob for blob in blobs}
    logging.info(f"Found {len(blob_dict)} dreams in Blob storage.")
    
    # Process each dream from Keep
    for dream in all_dreams:
        dream_id = dream.id
        # Use edited time if available, otherwise fall back to created time
        keep_last_modified = dream.timestamps.edited if dream.timestamps.edited else dream.timestamps.created
        
        # Check if dream exists in blob storage
        if dream_id in blob_dict:
            # Download the existing blob
            blob_client = container_client.get_blob_client(blob_dict[dream_id].name)
            blob_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
            
            # Get the last modified time from blob
            blob_last_modified = blob_data.get("lastModified")
            
            # If blob doesn't have lastModified or Keep's lastModified is newer, update blob
            if not blob_last_modified or keep_last_modified > datetime.datetime.fromisoformat(blob_last_modified):
                logging.info(f"Updating dream {dream_id} in blob storage (Keep modified: {keep_last_modified})")
                
                # Update the blob data with current Keep data
                blob_data["title"] = dream.title
                blob_data["labels"] = [label.name for label in dream.labels.all()]
                blob_data["text"] = dream.text
                blob_data["lastModified"] = str(keep_last_modified)
                
                # Upload the updated data
                blob_client.upload_blob(json.dumps(blob_data), overwrite=True)
            else:
                logging.info(f"Dream {dream_id} is up to date in blob storage")
    
    logging.info("Synchronization from Keep to Blob storage completed.")

def main(mytimer: func.TimerRequest) -> None:
    global keep, text_analytics_client, container_client, openai_client

    logging.info("Function triggered.")
    setup()

    # Get all dreams from Keep once
    logging.info("Fetching all dreams from Keep.")
    all_dreams = keep.find(labels=[keep.findLabel("Dream")])
    logging.info("Fetching existing dreams.")
    # First, synchronize existing dreams from Keep to Blob
    sync_dreams_from_keep_to_blob(all_dreams)

    logging.info("Fetching existing dreams from blob storage.")
    existing_dream_ids = [os.path.splitext(blob.name)[0] for blob in container_client.list_blobs()]
    
    # Process new dreams only - don't modify existing dreams
    new_dreams = [dream for dream in all_dreams if dream.id not in existing_dream_ids]
    logging.info(f"{len(new_dreams)} new dreams found.")

    if not new_dreams:
        logging.info("No new dreams to process.")
        return

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

        # Generate metadata (title and tags) with a single OpenAI call
        dream_metadata = generate_dream_metadata(dream.text)
        ai_title = dream_metadata["title"]
        new_tags = dream_metadata["tags"]
        logging.info(f"Generated metadata for dream {dream.id}: title='{ai_title}', tags={new_tags}")
        
        # Update Google Keep note with the AI-generated title, tags, and color
        update_success = update_keep_with_tags(dream, new_tags, ai_title)
        if update_success:
            logging.info(f"Successfully updated dream {dream.id} with AI metadata and formatting")
            # Update the parsed_dream with the new labels and title
            parsed_dream["labels"] = [label.name for label in dream.labels.all()]
            parsed_dream["title"] = dream.title
        else:
            logging.warning(f"Failed to update dream {dream.id} with AI metadata and formatting")

        parsed_dream["sentiment"] = sentiment
        parsed_dream["key_phrases"] = key_phrases,
        parsed_dream["entities"] = entities

        blob_client = container_client.get_blob_client(dream.id + ".json")
        blob_client.upload_blob(json.dumps(parsed_dream))
