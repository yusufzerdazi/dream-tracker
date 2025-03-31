# DreamTracker

A serverless Azure Functions application that retrieves dream notes from Google Keep, analyzes them with Azure AI, and stores the results in Azure Blob Storage.

## Features

- 🌙 Daily retrieval of dreams from Google Keep
- 🧠 AI-powered sentiment analysis, key phrase extraction, and entity recognition
- 🏷️ Automatic dream tagging using OpenAI to categorize dreams
- 💾 Persistent storage in Azure Blob Storage
- 📊 Dream summary statistics accessible via API endpoint
- 🔄 CORS support for frontend integration

## API Endpoints

- `GET /api/summary` - Get dream summary statistics organized by date
  - Allowed origins: `https://yusuf.zerdazi.com` and `http://localhost:5173`
  - Returns JSON with summary metrics including sentiment analysis, entity counts, tags, and key phrases

## Architecture

- **Azure Functions (Python)**: Serverless compute to run the daily dream analysis
- **Azure Blob Storage**: For storing analyzed dream data
- **Azure AI Text Analytics**: For analyzing dream content
- **OpenAI API**: For generating relevant dream tags
- **Azure Monitor**: For logging and monitoring
- **GitHub Actions**: For CI/CD deployment

## Prerequisites

- Azure subscription
- A Google account with Keep notes tagged with "Dream"
- OpenAI API key
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
       "CognitiveServicesKey": "your-cognitive-services-key",
       "OpenAIApiKey": "your-openai-api-key"
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
   - `GOOGLEEMAIL`: Your Google email for accessing Google Keep
   - `GOOGLEPASSWORD`: Your Google app password
   - `OPENAI_API_KEY`: Your OpenAI API key

2. Run the "Deploy Azure Infrastructure with Bicep" GitHub Actions workflow manually with your desired environment parameters.

### Application Deployment (GitHub Actions)

1. Create GitHub repository secrets:
   - `AZURE_FUNCTIONAPP_PUBLISH_PROFILE`: Azure Function App publish profile

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