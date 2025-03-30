import logging
import os
import azure.functions as func
from azure.storage.blob import BlobServiceClient
from DreamSummary import generate_or_update_summary

def main(mytimer: func.TimerRequest) -> None:
    """Timer trigger function for generating dream summaries on schedule."""
    logging.info('DreamSummaryScheduler timer trigger function executed.')
    
    # Skip if timer hasn't actually elapsed (for testing)
    if mytimer.past_due:
        logging.info('Timer is past due!')
        
    # Get storage connection
    storage_connection = os.getenv('StorageAccountConnectionString')
    if not storage_connection:
        logging.error("StorageAccountConnectionString environment variable is not set")
        return
    
    try:
        # Connect to storage
        blob_service_client = BlobServiceClient.from_connection_string(storage_connection)
        container_client = blob_service_client.get_container_client("dreams")
        
        # Generate or update the summary
        summary = generate_or_update_summary(container_client)
        if isinstance(summary, dict) and len(summary) > 0:
            logging.info(f"Summary generated with {len(summary)} date entries")
        else:
            logging.info("Summary generated but no entries found")
            
    except Exception as e:
        logging.error(f"Error in DreamSummaryScheduler: {str(e)}") 