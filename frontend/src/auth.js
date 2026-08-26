import Keycloak from "keycloak-js";

export const keycloak = new Keycloak({
  // Production deployments expose Keycloak through the same HTTPS gateway.
  // Keep the build argument override, but never send a production browser to
  // its own localhost when that argument is accidentally omitted.
  url: import.meta.env.VITE_KEYCLOAK_URL || `${window.location.origin}/auth`,
  realm: import.meta.env.VITE_KEYCLOAK_REALM || "rice-research",
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID || "rice-research-web",
});

let initialized = false;

export async function initializeAuthentication() {
  const authenticated = await keycloak.init({
    onLoad: "login-required",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });
  initialized = true;
  return authenticated;
}

export async function accessToken() {
  if (!initialized || !keycloak.authenticated) return null;
  await keycloak.updateToken(60);
  return keycloak.token || null;
}
