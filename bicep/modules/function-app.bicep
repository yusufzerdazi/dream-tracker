@description('The name of the function app that you wish to create.')
param functionAppName string

@description('Location for all resources.')
param location string

@description('The hosting plan id to host the function app.')
param hostingPlanId string

@description('Storage Account connection string for the function app.')
param storageConnectionString string

@description('The Application Insights instrumentation key.')
param appInsightsInstrumentationKey string

@description('The runtime language of the function app.')
param runtime string = 'python'

@description('The version of python to use.')
param pythonVersion string = '3.11'

@description('Function app settings')
param appSettings array = []

resource functionApp 'Microsoft.Web/sites@2022-03-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlanId
    siteConfig: {
      linuxFxVersion: 'Python|${pythonVersion}'
      appSettings: concat([
        {
          name: 'AzureWebJobsStorage'
          value: storageConnectionString
        }
        {
          name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING'
          value: storageConnectionString
        }
        {
          name: 'WEBSITE_CONTENTSHARE'
          value: toLower(functionAppName)
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsightsInstrumentationKey
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: runtime
        }
      ], appSettings)
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
    }
    httpsOnly: true
  }
}

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output defaultHostName string = functionApp.properties.defaultHostName 
