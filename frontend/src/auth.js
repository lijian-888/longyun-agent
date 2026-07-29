import Keycloak from "keycloak-js";

export const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL || "https://localhost:8443",
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
