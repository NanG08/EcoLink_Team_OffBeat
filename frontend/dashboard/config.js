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
 *   EcoLink-rate-table -> estimated_value calculation inside PickupsFn
 *   EcoLink-users     -> lat/lng for map pins (address privacy enforced server-side)
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
  baseUrl: 'https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_n6wa98s6n"',
  cognito: {
    userPoolId: 'ap-southeast-2_n6wa98s6n',
    clientId: '6v8kpqk2jesqpik47lipp77cjk',
    region: 'ap-southeast-2'
  },
  amazonLocation: {
    region: 'us-east-1',           // e.g. ap-south-1
    mapName: 'ecolink-dispatch-map',
    apiKey: 'v1.public.eyJqdGkiOiI3ODdjZDU1OC1lZWQ4LTRmZWYtOTAyMS04ZmZjMTlmMGFiYzgifTyZVe5K63x4F1uDF-7ami3oGaz-us_sSEmLmolovEpPTCRWdAjLZW0mO4x-goUuOrqztP0sNA8dYUiE94n4uZIVoXib5Jhxr7DADKCqyYYtfG5EiSwt5nOpjOkjcsD6CyGNKMt7-r3n0AA1Y-xk9_ackmqQgeQhKZhQdKISzhmpMQGHHCj38DNNz5K7Rom5Ny3JZX8R2NcZwBxDvH7Yp9I3ZOiwXP_TX7GyonW1pqSkdVPuZEyEsPlBUlpTFQSW_eRSctLvj-tHMXd3nDvXzLILebttKO4AcZQddM06PIJqSu4QILkN7-6L0WcqCPT2y1tLREaf9q3X2vZoWSnwRDE.ZWU0ZWIzMTktMWRhNi00Mzg0LTllMzYtNzlmMDU3MjRmYTkx'
  }
};
