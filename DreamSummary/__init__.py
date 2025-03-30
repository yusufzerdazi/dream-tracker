import datetime
import logging
import os
import json
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Union

import azure.functions as func
from azure.storage.blob import BlobServiceClient

def get_last_run_date(container_client) -> Optional[datetime.datetime]:
    """Get the last run date from the summary metadata blob if it exists."""
    try:
        summary_metadata_blob = container_client.get_blob_client("summary_metadata.json")
        metadata_content = summary_metadata_blob.download_blob().readall()
        metadata = json.loads(metadata_content)
        return datetime.datetime.fromisoformat(metadata.get("last_run_date"))
    except Exception as e:
        logging.info(f"No previous summary metadata found or error: {str(e)}")
        return None

def update_last_run_date(container_client):
    """Update the last run date in the summary metadata blob."""
    summary_metadata_blob = container_client.get_blob_client("summary_metadata.json")
    metadata = {"last_run_date": datetime.datetime.now().isoformat()}
    summary_metadata_blob.upload_blob(json.dumps(metadata), overwrite=True)

def load_dream_data(container_client, last_run_date: Optional[datetime.datetime] = None) -> List[Dict]:
    """Load all dream data or only new dreams since last run."""
    dream_data = []
    
    for blob in container_client.list_blobs():
        # Skip non-dream files
        if not blob.name.endswith('.json') or blob.name == "summary.json" or blob.name == "summary_metadata.json":
            continue
            
        # Check if we need to process this dream based on last run date
        if last_run_date is not None:
            # Convert blob last_modified to datetime for comparison
            if blob.last_modified.replace(tzinfo=None) <= last_run_date:
                continue
                
        blob_client = container_client.get_blob_client(blob.name)
        dream_content = blob_client.download_blob().readall()
        dream = json.loads(dream_content)
        dream_data.append(dream)
    
    return dream_data

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
        for dream in daily_dreams:
            entities = dream.get("entities", [])
            if entities:
                all_entities.extend([e["text"] for e in entities])
            
            # Key phrases is a tuple with the first element being the list (due to comma in original code)
            key_phrases = dream.get("key_phrases", ([], None))[0]
            if key_phrases:
                all_key_phrases.extend(key_phrases)
        
        # Entity frequency
        entity_freq = defaultdict(int)
        for entity in all_entities:
            entity_freq[entity] += 1
        
        # Top entities
        top_entities = sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)[:5]
        
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
            "key_phrases": {
                "count": len(all_key_phrases),
                "sample": all_key_phrases[:10] if all_key_phrases else []
            }
        }
    
    return summary

def generate_or_update_summary(container_client) -> Dict:
    """Generate a new summary or update the existing one."""
    try:
        # Get the last run date
        last_run_date = get_last_run_date(container_client)
        
        # Load dream data (only new dreams if we have a last run date)
        dream_data = load_dream_data(container_client, last_run_date)
        
        if not dream_data and last_run_date:
            # No new dreams since last run, load the existing summary
            try:
                summary_blob = container_client.get_blob_client("summary.json")
                existing_summary = json.loads(summary_blob.download_blob().readall())
                return existing_summary
            except Exception as e:
                logging.warning(f"Error loading existing summary: {str(e)}")
                # If we can't load existing summary, process all dreams
                dream_data = load_dream_data(container_client)
        
        # Generate new summary if we have dreams to process
        if dream_data:
            new_summary = generate_summary(dream_data)
            
            # If we have an existing summary, merge the new data
            if last_run_date:
                try:
                    summary_blob = container_client.get_blob_client("summary.json")
                    existing_summary = json.loads(summary_blob.download_blob().readall())
                    
                    # Merge summaries (new data overrides old data for same dates)
                    for date, data in new_summary.items():
                        existing_summary[date] = data
                    
                    new_summary = existing_summary
                except Exception as e:
                    logging.warning(f"Error merging with existing summary: {str(e)}")
            
            # Save the summary
            summary_blob = container_client.get_blob_client("summary.json")
            summary_blob.upload_blob(json.dumps(new_summary), overwrite=True)
            
            # Update the last run date
            update_last_run_date(container_client)
            
            return new_summary
        else:
            return {"message": "No dreams to analyze"}
            
    except Exception as e:
        logging.error(f"Error generating summary: {str(e)}")
        return {"error": str(e)}

def main(mytimer: func.TimerRequest, req: func.HttpRequest = None) -> func.HttpResponse:
    """Function entry point - handles both timer and HTTP triggers."""
    # Get storage connection
    storage_connection = os.getenv('StorageAccountConnectionString')
    blob_service_client = BlobServiceClient.from_connection_string(storage_connection)
    container_client = blob_service_client.get_container_client("dreams")
    
    # If triggered by HTTP request
    if req:
        logging.info('Processing HTTP request for dream summary')
        
        # Set CORS headers
        headers = {
            "Access-Control-Allow-Origin": "https://yusuf.zerdazi.com,http://localhost:5173",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Content-Type",
        }
        
        # Handle OPTIONS request (CORS preflight)
        if req.method == "OPTIONS":
            return func.HttpResponse(status_code=200, headers=headers)
        
        try:
            # Try to get existing summary first for faster response
            try:
                summary_blob = container_client.get_blob_client("summary.json")
                summary = json.loads(summary_blob.download_blob().readall())
            except Exception:
                # If no summary exists, generate it
                summary = generate_or_update_summary(container_client)
                
            return func.HttpResponse(
                body=json.dumps(summary),
                mimetype="application/json",
                headers=headers
            )
        except Exception as e:
            return func.HttpResponse(
                body=json.dumps({"error": str(e)}),
                status_code=500,
                mimetype="application/json",
                headers=headers
            )
    # If triggered by timer
    else:
        logging.info('Python timer trigger function executed.')
        
        # Skip if timer hasn't actually elapsed (for testing)
        if mytimer.past_due:
            logging.info('Timer is past due!')
            
        # Generate or update the summary
        summary = generate_or_update_summary(container_client)
        logging.info(f"Summary generated with {len(summary)} date entries")
        
        return None 