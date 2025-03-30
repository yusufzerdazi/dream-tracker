# DreamSummaryScheduler - Timer Trigger

This function runs on a daily schedule (at 1:00 AM) to update the dream summary statistics. It processes all dream data collected since the last run and updates the summary.json file in the blob storage.

## Schedule

The function runs on the following cron schedule:
`0 0 1 * * *` (at 1:00 AM every day)

## Dependencies

- This function depends on the DreamSummary module for the summary generation logic
- Requires the StorageAccountConnectionString connection string in application settings 