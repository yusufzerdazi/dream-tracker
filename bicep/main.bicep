@description('The name of the function app that you wish to create.')
param appName string = 'dreamtracker'

@description('The email of the Google account.')
param googleEmail string

@description('The password of the Google account.')
@secure()
param googlePassword string

@description('Storage Account type')
@allowed([
  'Standard_LRS'
  'Standard_GRS'
  'Standard_RAGRS'
])
param storageAccountType string = 'Standard_LRS'

@description('Location for all resources.')
param location string = resourceGroup().location

@description('The language worker runtime to load in the function app.')
@allowed([
  'python'
])
param runtime string = 'python'

@description('The version of python to use.')
param pythonVersion string = '3.11'

@description('The SKU of App Service Plan')
param sku string = 'Y1'

var functionAppName = appName
var hostingPlanName = appName
var applicationInsightsName = appName
var storageAccountName = 'dreamtracker'
var containerName = 'dreams'
var cognitiveServicesAccountName = 'dreamtracker1'
var functionWorkerRuntime = runtime

resource storageAccount 'Microsoft.Storage/storageAccounts@2022-09-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: storageAccountType
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2022-09-01' = {
  name: '${storageAccountName}/default/${containerName}'
  properties: {
    publicAccess: 'None'
  }
  dependsOn: [
    storageAccount
  ]
}

resource hostingPlan 'Microsoft.Web/serverfarms@2022-03-01' = {
  name: hostingPlanName
  location: location
  sku: {
    name: sku
    tier: 'Dynamic'
  }
  properties: {}
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Request_Source: 'IbizaWebAppExtensionCreate'
    RetentionInDays: 90
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource cognitiveServices 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: cognitiveServicesAccountName
  location: location
  kind: 'TextAnalytics'
  sku: {
    name: 'S'
  }
  properties: {
    customSubDomainName: cognitiveServicesAccountName
    publicNetworkAccess: 'Enabled'
  }
}

module functionApp 'modules/function-app.bicep' = {
  name: 'functionAppDeploy'
  params: {
    functionAppName: functionAppName
    location: location
    hostingPlanId: hostingPlan.id
    storageConnectionString: 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
    appInsightsInstrumentationKey: applicationInsights.properties.InstrumentationKey
    runtime: functionWorkerRuntime
    pythonVersion: pythonVersion
    appSettings: [
      {
        name: 'StorageAccountConnectionString'
        value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
      }
      {
        name: 'CognitiveServicesEndpoint'
        value: cognitiveServices.properties.endpoint
      }
      {
        name: 'CognitiveServicesKey'
        value: cognitiveServices.listKeys().key1
      }
      {
        name: 'GoogleEmail'
        value: googleEmail
      }
      {
        name: 'GooglePassword'
        value: googlePassword
      }
    ]
  }
}

// Outputs
output functionAppUrl string = 'https://${functionApp.outputs.defaultHostName}'
output storageAccountName string = storageAccountName
output cognitiveServicesEndpoint string = cognitiveServices.properties.endpoint 
