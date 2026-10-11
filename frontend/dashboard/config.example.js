/**
 * Copy this file to config.js and fill in your values.
 * config.js is git-ignored — never commit it.
 *
 * CDK outputs (after `cdk deploy`):
 *   ApiUrl           -> baseUrl
 *   UserPoolId       -> cognito.userPoolId
 *   UserPoolClientId -> cognito.clientId
 *
 * Amazon Location API key:
 *   AWS Console -> Amazon Location Service -> API keys -> Create
 *   Attach to your map resource with geo:GetMap* action.
 *   Add to .env as: AMAZON_LOCATION_API_KEY=v1.public.eyJ...
 */
window.ECOLINK_CONFIG = {
  mockMode: true,
  baseUrl: 'https://<api-id>.execute-api.<region>.amazonaws.com/prod',
  cognito: {
    userPoolId: '<region>_<id>',
    clientId: '<client-id>',
    region: '<region>'
  },
  amazonLocation: {
    region: '<region>',
    mapName: 'ecolink-dispatch-map',
    apiKey: '<your-amazon-location-api-key>'
  }
};
