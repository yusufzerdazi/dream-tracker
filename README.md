# DreamTracker

A serverless Azure Functions application that retrieves dream notes from Google Keep, analyzes them with Azure AI, and stores the results in Azure Blob Storage.

## Features

- 🌙 Daily retrieval of dreams from Google Keep
- 🧠 AI-powered sentiment analysis, key phrase extraction, and entity recognition
- 💾 Persistent storage in Azure Blob Storage

## Architecture

- **Azure Functions (Python)**: Serverless compute to run the daily dream analysis
- **Azure Blob Storage**: For storing analyzed dream data
- **Azure AI Text Analytics**: For analyzing dream content
- **Azure Monitor**: For logging and monitoring
- **GitHub Actions**: For CI/CD deployment

## Prerequisites

- Azure subscription
- A Google account with Keep notes tagged with "Dream"
- GitHub account (for deployment)

## Local Development

1. Clone this repository
2. Create a Python virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a `local.settings.json` file:
   ```json
   {
     "IsEncrypted": false,
     "Values": {
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "GoogleEmail": "your-email@gmail.com",
       "GooglePassword": "your-app-password",
       "StorageAccountConnectionString": "your-storage-connection-string",
       "CognitiveServicesEndpoint": "your-cognitive-services-endpoint",
       "CognitiveServicesKey": "your-cognitive-services-key"
     }
   }
   ```
5. Run the Azure Functions Core Tools:
   ```bash
   func start
   ```

## Deployment

### Infrastructure Deployment (GitHub Actions)

1. Create GitHub repository secrets:
   - `AZURE_CREDENTIALS`: Azure service principal credentials in JSON format

2. Run the "Deploy Azure Infrastructure with Bicep" GitHub Actions workflow manually with your desired environment parameters.

### Application Deployment (GitHub Actions)

1. Create GitHub repository secrets:
   - `AZURE_FUNCTIONAPP_PUBLISH_PROFILE`: Azure Function App publish profile
   - Add application settings in the Azure portal:
     - `GoogleEmail`: Your Google email
     - `GooglePassword`: Your Google app password

2. Push your changes to the main branch or manually trigger the "Deploy DreamTracker to Azure Functions" workflow.

## Infrastructure as Code (Bicep)

The `infra/main.bicep` file defines all required Azure resources:
- Azure Function App (Linux, Python)
- Storage Account
- Application Insights
- Log Analytics Workspace
- Cognitive Services Account

## License

MIT 