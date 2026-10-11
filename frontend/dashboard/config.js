/**
 * EcoLink Operations Dashboard configuration.
 *
 * Demo (default): mockMode true -> built-in sample data, no backend needed.
 * Live: set mockMode to false and fill in the CDK outputs below, then sign in with a
 *       Cognito user from the pool (see backend/README.md, "Create a dashboard user").
 *
 * CDK outputs to copy here after `cdk deploy`:
 *   ApiUrl            -> baseUrl
 *   UserPoolId        -> cognito.userPoolId
 *   UserPoolClientId  -> cognito.clientId
 *
 * DynamoDB tables used by the dashboard (read via API Gateway -> Lambda -> DynamoDB):
 *   EcoLink-pickups   -> GET /pickups, GET /pickups/{id}, PATCH /dashboard/pickups/{id}
 *   EcoLink-recyclers -> GET /dashboard/recyclers
 *   EcoLink-conversation-state -> GET /dashboard/users (joined server-side)
 *   EcoLink-rate-table -> estimated_value calculation inside PickupsFn
 *   EcoLink-users     -> WhatsApp users and lat/lng for map pins (address privacy enforced server-side)
 *
 * Seed demo data into DynamoDB first:
 *   python scripts/seed.py --recycler-phone +91XXXXXXXXXX --user-id whatsapp:+91YYYYYYYYY
 *
 * Amazon Location Service (for the Live Dispatch map):
 *   1. Go to AWS Console -> Amazon Location Service -> Maps -> Create map
 *      Name: ecolink-dispatch-map  Style: Standard Light (or Explore)
 *   2. Go to API keys -> Create API key
 *      Name: ecolink-dashboard-key  Resource: arn:aws:geo:<region>:<account>:map/ecolink-dispatch-map
 *      Actions: geo:GetMap*
 *   3. Fill in mapName and apiKey below.
 */
window.ECOLINK_CONFIG = {
  mockMode: false,
  // Replace <ApiGatewayId> with the ApiUrl value printed by `cdk deploy`.
  baseUrl: 'https://t2ilaf2bl1.execute-api.ap-southeast-2.amazonaws.com/prod',
  cognito: {
    userPoolId: 'ap-southeast-2_n6wa98s6n',
    clientId: '6v8kpqk2jesqpik47lipp77cjk',
    region: 'ap-southeast-2'
  },
  amazonLocation: {
    region: 'ap-southeast-2',
    mapName: 'ecolink-dispatch-map',
    apiKey: document.querySelector('meta[name="amazon-location-key"]')?.content || ''
  }
};
