/**
 * EcoLink Operations Dashboard configuration template.
 *
 * Copy this file to config.js and fill in your values:
 *   cp frontend/dashboard/config.example.js frontend/dashboard/config.js
 *
 * config.js is in .gitignore and must NEVER be committed.
 *
 * Where to get each value:
 *   baseUrl          -> "ApiUrl" output of `cdk deploy`
 *   userPoolId       -> "UserPoolId" output of `cdk deploy`
 *   clientId         -> "UserPoolClientId" output of `cdk deploy`
 *   region           -> AWS region you deployed to (e.g. ap-south-1)
 *   mapName          -> Name of your Amazon Location Service map
 *   apiKey           -> Amazon Location Service API key (geo:GetMap* on that map)
 *
 * To use demo mode (no backend needed), set mockMode: true.
 */
window.ECOLINK_CONFIG = {
  mockMode: true,
  baseUrl: 'https://<api-id>.execute-api.<region>.amazonaws.com/prod',
  cognito: {
    userPoolId: '<UserPoolId>',
    clientId: '<UserPoolClientId>',
    region: '<region>'
  },
  amazonLocation: {
    region: '<region>',
    mapName: 'ecolink-dispatch-map',
    apiKey: '<AmazonLocationApiKey>'
  }
};
