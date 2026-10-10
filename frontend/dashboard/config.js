/**
 * EcoLink Operations Dashboard configuration.
 *
 * Demo (default): mockMode true -> built-in sample data, no backend needed.
 * Live: set mockMode to false and baseUrl to the "ApiUrl" output of `cdk deploy`
 *       (e.g. "https://abc123.execute-api.ap-south-1.amazonaws.com/prod"), then sign in with a
 *       Cognito user from the pool (see backend/README.md, "Create a dashboard user").
 *       The page must be served from the origin allowed by CORS_ORIGIN ("*" by default).
 */
window.ECOLINK_CONFIG = {
  mockMode: true,
  baseUrl: ''
};
