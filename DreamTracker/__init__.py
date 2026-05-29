import datetime
import logging
import os
import json
import re

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

# ---------------------------------------------------------------------------
# Dream tag taxonomy and analyzer prompt — source of truth lives here, in git.
# ---------------------------------------------------------------------------

# The only tags the analyzer may assign. Enforced at the model layer via the
# JSON schema enum below, and used to repair invalid/legacy tags. "Wet" and
# "Sexual" are valid labels but are excluded from the public summary API
# (see EXCLUDED_TAGS, consumed by DreamSummary).
ALLOWED_TAGS = ["Inspiration", "Wet", "Lucid", "Sexual", "Nightmare"]

# Prompt template; {dream_text} is substituted at call time.
DREAM_ANALYZER_PROMPT = (
    "Analyze the following dream and provide: "
    "1. A short title (3-6 words) that captures the dream's essence. "
    "2. Relevant tags from the following list of labels: "
    "- Inspiration: The dream has a significant meaning which sticks with me "
    "into the day. This could be an idea for a song, a game or something which "
    "shifts my worldview. A very small percentage of dreams fall into this "
    "category. "
    "- Wet: I cum / ejaculate during the dream. If there's a phrase like "
    "\"i cum\" add this. "
    "- Lucid: I am aware that I am dreaming. "
    "- Sexual: References sex. "
    "- Nightmare: the dream is genuinely scary in a way which continues into "
    "waking. "
    "No other labels should be used, and only assign a tag when it genuinely "
    "applies (the tags array may be empty). Note that a dream can be sexual "
    "without being wet. "
    "Dream text: <<<{dream_text}>>>"
)

# Strict structured-output schema. With strict=True plus the enum on tag items,
# the model can only ever return the allowed tags in the required shape.
DREAM_METADATA_SCHEMA = {
    "name": "dream_metadata",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "A short 3-6 word title capturing the dream's essence",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "enum": ALLOWED_TAGS},
                "description": "Applicable tags from the allowed list; empty if none apply",
            },
        },
        "required": ["title", "tags"],
        "additionalProperties": False,
    },
}


def canonical_tag(name):
    """Map a label name to its canonical allowed tag, or None if it isn't one.

    Recognises exact matches (case-insensitive) and the common invalid form
    with a trailing " Dream" (e.g. "Nightmare Dream" -> "Nightmare"). Labels
    that aren't one of our managed tags — including the master "Dream" label
    and any unrelated user labels — return None and are left untouched.
    """
    if not name:
        return None
    by_lower = {t.lower(): t for t in ALLOWED_TAGS}
    key = name.strip().lower()
    if key in by_lower:
        return by_lower[key]
    if key.endswith(" dream"):
        stripped = key[:-len(" dream")].strip()
        if stripped in by_lower:
            return by_lower[stripped]
    return None


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

# Leading UK-format date prefix ("DD/MM/YYYY", optionally followed by ": <title>").
# When a note title already carries such a date, it is treated as the
# authoritative dream date (overriding the Keep note's creation timestamp).
TITLE_DATE_RE = re.compile(r'^\s*(\d{2})/(\d{2})/(\d{4})')


def parse_title_date(title):
    """Extract the dream date from a note title's leading DD/MM/YYYY prefix.

    Returns a datetime (at midnight) or None if the title has no date prefix.
    """
    if not title:
        return None
    match = TITLE_DATE_RE.match(title)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return datetime.datetime(year, month, day)
    except ValueError:
        return None


def dream_date(keep_dream):
    """The date a dream was recorded: from the title prefix if present,
    otherwise the Keep note's creation timestamp."""
    return parse_title_date(keep_dream.title) or keep_dream.timestamps.created


def parse_dream(keep_dream):
    # Use edited time if available, otherwise fall back to created time
    last_modified = keep_dream.timestamps.edited if keep_dream.timestamps.edited else keep_dream.timestamps.created

    return {
        "id": keep_dream.id,
        "title": keep_dream.title,
        "labels": [label.name for label in keep_dream.labels.all()],
        "timestamp": str(dream_date(keep_dream)),
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
    """Generate a title and allowed tags for a dream via a single OpenAI call
    using strict structured output (schema-enforced shape and tag enum)."""
    try:
        prompt = DREAM_ANALYZER_PROMPT.format(dream_text=dream_text)

        response = openai_client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content": "You are a dream analyzer that creates meaningful titles and tags for dreams."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_schema", "json_schema": DREAM_METADATA_SCHEMA},
        )

        content = response.choices[0].message.content.strip()
        metadata = json.loads(content)

        # The strict schema guarantees the shape and the tag enum, but guard
        # defensively against empty/unexpected content.
        title = metadata.get("title") or "Untitled Dream"
        tags = [t for t in metadata.get("tags", []) if t in ALLOWED_TAGS]
        return {"title": title, "tags": tags}
    except Exception as e:
        logging.error(f"Error generating dream metadata: {str(e)}")
        return {"title": "Untitled Dream", "tags": []}

def update_keep_with_tags(dream, new_tags, ai_title):
    """Update Google Keep note with new tags, format title, and set color."""
    try:
        # Format the title with date prefix "DD/MM/YYYY: <dream title>".
        # Prefer the date already embedded in the title (derived from the
        # recording's creation metadata); fall back to the Keep creation time.
        date_prefix = dream_date(dream).strftime("%d/%m/%Y")

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

def is_untitled(dream):
    """True if the note title shows metadata generation previously failed
    (the 'Untitled Dream' fallback), so it should be regenerated."""
    return "untitled dream" in (dream.title or "").lower()

def _update_blob_labels(dream):
    """Overwrite a stored dream's label list to match Keep (used after a tag
    repair, so the summary API reflects the corrected tags)."""
    blob_client = container_client.get_blob_client(f"{dream.id}.json")
    blob_data = json.loads(blob_client.download_blob().readall().decode('utf-8'))
    blob_data["labels"] = [label.name for label in dream.labels.all()]
    blob_client.upload_blob(json.dumps(blob_data), overwrite=True)

def repair_invalid_tags(all_dreams, blob_ids):
    """Remap invalid/legacy tag labels to their canonical form across all dreams
    (e.g. 'Nightmare Dream' -> 'Nightmare'), in both Keep and blob storage.
    Leaves the master 'Dream' label and any unrelated labels untouched."""
    keep_changed = False
    for dream in all_dreams:
        dream_changed = False
        for label in list(dream.labels.all()):
            canon = canonical_tag(label.name)
            if canon and canon != label.name:
                logging.info(f"Remapping tag '{label.name}' -> '{canon}' on dream {dream.id}")
                dream.labels.remove(label)
                canon_label = keep.findLabel(canon) or keep.createLabel(canon)
                dream.labels.add(canon_label)
                dream_changed = True
        if dream_changed:
            keep_changed = True
            # Keep blob labels in sync for dreams already stored (others get
            # written with correct labels when processed).
            if dream.id in blob_ids:
                try:
                    _update_blob_labels(dream)
                except Exception as e:
                    logging.warning(f"Could not update blob labels for {dream.id}: {e}")
    if keep_changed:
        keep.sync()
        logging.info("Invalid tag repair complete; synced to Keep.")

def main(mytimer: func.TimerRequest) -> None:
    global keep, text_analytics_client, container_client, openai_client

    logging.info("Function triggered.")
    setup()

    # Get all dreams from Keep once
    logging.info("Fetching all dreams from Keep.")
    dream_label = keep.findLabel("Dream")
    if not dream_label:
        logging.error("Could not find 'Dream' label in Google Keep!")
        return
    
    logging.info(f"Found Dream label with ID: {dream_label.id}")
    all_dreams = [*keep.find(labels=[dream_label])]
    logging.info(f"Found {len(all_dreams)} dreams in Keep")
    
    # Log details of each dream found
    for dream in all_dreams:
        logging.info(f"Dream ID: {dream.id}, Title: {dream.title}, Created: {dream.timestamps.created}, Modified: {dream.timestamps.edited}")
    
    # Identify what is already stored before making changes.
    existing_dream_ids = set(os.path.splitext(blob.name)[0] for blob in container_client.list_blobs())

    # Repair invalid/legacy tags (e.g. "Nightmare Dream" -> "Nightmare") across
    # every dream, in Keep and blob, before syncing.
    repair_invalid_tags(all_dreams, existing_dream_ids)

    logging.info("Synchronizing dreams from Keep to blob storage.")
    sync_dreams_from_keep_to_blob(all_dreams)

    # Process brand-new dreams, plus any previously-failed dreams still titled
    # "Untitled Dream" (regenerate their title + tags).
    new_dreams = [dream for dream in all_dreams if dream.id not in existing_dream_ids]
    untitled_dreams = [dream for dream in all_dreams if dream.id in existing_dream_ids and is_untitled(dream)]
    dreams_to_process = new_dreams + untitled_dreams
    logging.info(f"{len(new_dreams)} new dreams; {len(untitled_dreams)} untitled dreams to regenerate.")

    if not dreams_to_process:
        logging.info("No dreams to process.")
        return

    cog_dreams = batch([{"id": dream.id, "text": dream.text} for dream in dreams_to_process], 5)
    cog_result_sentiment = []
    cog_result_key_phrases = []
    cog_result_entities = []

    logging.info("Analysing dreams.")
    for cog_dream_batch in cog_dreams:
        cog_result_sentiment.extend(text_analytics_client.analyze_sentiment(cog_dream_batch))
        cog_result_key_phrases.extend(text_analytics_client.extract_key_phrases(cog_dream_batch))
        cog_result_entities.extend(text_analytics_client.recognize_entities(cog_dream_batch))

    logging.info("Uploading dreams.")
    for dream in dreams_to_process:
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
        blob_client.upload_blob(json.dumps(parsed_dream), overwrite=True)
