# DreamSummaryScheduler - Timer Trigger

This function runs on a daily schedule (at 1:00 AM) to update the dream summary statistics. It processes all dream data and regenerates the summary.json file in the blob storage, but only if the existing summary is older than 24 hours.

## Schedule

The function runs on the following cron schedule:
`0 0 1 * * *` (at 1:00 AM every day)

## Performance Optimization

For better performance, the function:
- Checks if the existing summary.json was refreshed within the last 24 hours
- If the summary is fresh (< 24 hours old), returns it directly without processing
- If the summary is stale (≥ 24 hours old) or doesn't exist, regenerates it from all dreams

## Dependencies

- This function depends on the DreamSummary module for the summary generation logic
- Requires the StorageAccountConnectionString connection string in application settings 