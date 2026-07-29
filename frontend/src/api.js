import { accessToken, keycloak } from "./auth";

export async function authorizedFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = await accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && path.startsWith("/api/")) {
    keycloak.login();
  }
  return response;
}

export async function request(path, options = {}) {
  const response = await authorizedFetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(typeof body === "string" ? body : body.detail || "请求失败");
  }
  return body;
}

export function jsonRequest(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}
