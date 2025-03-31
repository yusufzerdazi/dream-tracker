import datetime
import logging
import os
import json
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Union

import azure.functions as func
from azure.storage.blob import BlobServiceClient

def blob_exists(blob_client) -> bool:
    """Check if a blob exists without downloading it."""
    try:
        blob_client.get_blob_properties()
        return True
    except Exception:
        return False

def load_dream_data(container_client) -> List[Dict]:
    """Load all dream data."""
    dream_data = []
    has_dreams = False
    
    for blob in container_client.list_blobs():
        # Detect if we have any dream files at all
        if blob.name.endswith('.json') and blob.name != "summary.json" and blob.name != "summary_metadata.json":
            has_dreams = True
            
        # Skip non-dream files
        if not blob.name.endswith('.json') or blob.name == "summary.json" or blob.name == "summary_metadata.json":
            continue
                
        blob_client = container_client.get_blob_client(blob.name)
        dream_content = blob_client.download_blob().readall()
        dream = json.loads(dream_content)
        dream_data.append(dream)
    
    return dream_data, has_dreams

def generate_summary(dreams: List[Dict]) -> Dict:
    """Generate summary statistics from dream data."""
    if not dreams:
        return {"message": "No dreams to analyze"}
        
    # Group dreams by date (only looking at the date portion of the timestamp)
    dreams_by_date = defaultdict(list)
    for dream in dreams:
        dream_date = dream["timestamp"].split()[0]  # Extract date part
        dreams_by_date[dream_date].append(dream)
    
    # Calculate summary for each date
    summary = {}
    for date, daily_dreams in dreams_by_date.items():
        # Sentiment metrics
        positive_scores = [d["sentiment"]["confidence_scores"]["positive"] for d in daily_dreams]
        neutral_scores = [d["sentiment"]["confidence_scores"]["neutral"] for d in daily_dreams]
        negative_scores = [d["sentiment"]["confidence_scores"]["negative"] for d in daily_dreams]
        
        # Entity and key phrase counts
        all_entities = []
        all_key_phrases = []
        all_tags = []
        
        for dream in daily_dreams:
            entities = dream.get("entities", [])
            if entities:
                all_entities.extend([e["text"] for e in entities])
            
            # Key phrases is a tuple with the first element being the list (due to comma in original code)
            key_phrases = dream.get("key_phrases", ([], None))[0]
            if key_phrases:
                all_key_phrases.extend(key_phrases)
                
            # Collect all tags/labels from dreams
            labels = dream.get("labels", [])
            if labels:
                all_tags.extend(labels)
        
        # Entity frequency
        entity_freq = defaultdict(int)
        for entity in all_entities:
            entity_freq[entity] += 1
        
        # Tag frequency
        tag_freq = defaultdict(int)
        for tag in all_tags:
            tag_freq[tag] += 1
        
        # Top entities and tags
        top_entities = sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)[:5]
        top_tags = sorted(tag_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Date summary
        summary[date] = {
            "dream_count": len(daily_dreams),
            "sentiment": {
                "avg_positive": statistics.mean(positive_scores) if positive_scores else 0,
                "avg_neutral": statistics.mean(neutral_scores) if neutral_scores else 0,
                "avg_negative": statistics.mean(negative_scores) if negative_scores else 0,
            },
            "entities": {
                "count": len(all_entities),
                "top_entities": dict(top_entities)
            },
            "tags": {
                "count": len(all_tags),
                "top_tags": dict(top_tags)
            },
            "key_phrases": {
                "count": len(all_key_phrases),
                "sample": all_key_phrases[:10] if all_key_phrases else []
            }
        }
    
    return summary

def generate_summary_from_all_dreams(container_client) -> Dict:
    """Generate a new summary from all dreams."""
    try:
        # Check if summary already exists and is fresh (less than 24 hours old)
        summary_blob = container_client.get_blob_client("summary.json")
        if blob_exists(summary_blob):
            # Get the last modified time of the summary blob
            properties = summary_blob.get_blob_properties()
            last_modified = properties.last_modified
            
            # Calculate time difference
            time_difference = datetime.datetime.now(datetime.timezone.utc) - last_modified
            
            # If the summary is less than 24 hours old, return it directly
            if time_difference.total_seconds() < 24 * 60 * 60:  # 24 hours in seconds
                logging.info(f"Using existing summary that was updated {time_difference.total_seconds() / 3600:.2f} hours ago")
                summary_content = summary_blob.download_blob().readall()
                return json.loads(summary_content)
            else:
                logging.info(f"Summary is {time_difference.total_seconds() / 3600:.2f} hours old - regenerating")
        
        # Load all dream data
        dream_data, has_dreams = load_dream_data(container_client)
        
        if not dream_data:
            return {"message": "No dreams to analyze"}
        
        # Generate new summary
        summary = generate_summary(dream_data)
        
        # Save the summary
        summary_blob.upload_blob(json.dumps(summary), overwrite=True)
        
        return summary
            
    except Exception as e:
        logging.error(f"Error generating summary: {str(e)}")
        return {"error": str(e)}

def main(req: func.HttpRequest) -> func.HttpResponse:
    """Function entry point - handles HTTP trigger."""
    # Get storage connection
    storage_connection = os.getenv('StorageAccountConnectionString')
    if not storage_connection:
        return func.HttpResponse(
            body=json.dumps({"error": "Storage connection string not set"}),
            status_code=500,
            mimetype="application/json"
        )

    # Load excluded tag categories from environment variables
    excluded_tags = os.getenv('EXCLUDED_TAGS', '')
    excluded_tags_list = [tag.strip().lower() for tag in excluded_tags.split(',') if tag.strip()]
    logging.info(f"Excluding tags: {excluded_tags_list}")

    # Set CORS headers
    headers = {
        "Access-Control-Allow-Origin": "https://yusuf.zerdazi.com,http://localhost:5173,https://portal.azure.com",
        "Access-Control-Allow-Methods": "GET",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    
    # Handle OPTIONS request (CORS preflight)
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers=headers)
    
    try:
        # Check for force refresh parameter
        force_refresh = req.params.get('force') == 'true'
        
        # Connect to storage
        blob_service_client = BlobServiceClient.from_connection_string(storage_connection)
        container_client = blob_service_client.get_container_client("dreams")
        
        # Check if summary exists and is fresh (unless force refresh is requested)
        if not force_refresh:
            summary_blob = container_client.get_blob_client("summary.json")
            if blob_exists(summary_blob):
                # Get the summary directly
                summary_content = summary_blob.download_blob().readall()
                summary = json.loads(summary_content)
                
                # Include cache info in the response
                properties = summary_blob.get_blob_properties()
                last_modified = properties.last_modified
                time_difference = datetime.datetime.now(datetime.timezone.utc) - last_modified
                summary["_metadata"] = {
                    "last_updated": last_modified.isoformat(),
                    "age_hours": round(time_difference.total_seconds() / 3600, 2)
                }
                
                # Filter summary to include tags but exclude sensitive ones
                filtered_summary = {}
                for date, data in summary.items():
                    if date == "_metadata":
                        filtered_summary[date] = data
                        continue
                        
                    filtered_summary[date] = {
                        "dream_count": data.get("dream_count", 0),
                        "sentiment": data.get("sentiment", {})
                    }
                    
                    # Include tags but filter out sensitive ones
                    if "tags" in data and "top_tags" in data["tags"]:
                        filtered_tags = {k: v for k, v in data["tags"]["top_tags"].items()
                                      if k.lower() not in excluded_tags_list}
                        filtered_summary[date]["tags"] = {
                            "count": data["tags"].get("count", 0),
                            "top_tags": filtered_tags
                        }
                
                return func.HttpResponse(
                    body=json.dumps(filtered_summary),
                    mimetype="application/json",
                    headers=headers
                )
        
        # Generate or regenerate summary
        summary = generate_summary_from_all_dreams(container_client)
        
        # Filter summary to include tags but exclude sensitive ones
        filtered_summary = {}
        for date, data in summary.items():
            if date == "_metadata" or date == "message" or date == "error":
                filtered_summary[date] = data
                continue
                
            filtered_summary[date] = {
                "dream_count": data.get("dream_count", 0),
                "sentiment": data.get("sentiment", {})
            }
            
            # Include tags but filter out sensitive ones
            if "tags" in data and "top_tags" in data["tags"]:
                filtered_tags = {k: v for k, v in data["tags"]["top_tags"].items()
                              if k.lower() not in excluded_tags_list}
                filtered_summary[date]["tags"] = {
                    "count": data["tags"].get("count", 0),
                    "top_tags": filtered_tags
                }
        
        return func.HttpResponse(
            body=json.dumps(filtered_summary),
            mimetype="application/json",
            headers=headers
        )
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return func.HttpResponse(
            body=json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
            headers=headers
        ) 