import { accessToken, keycloak } from "./auth";

function formatApiError(body, fallback = "请求失败") {
  if (typeof body === "string") return body.trim() || fallback;
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      const location = Array.isArray(item?.loc) ? item.loc.filter((value) => value !== "query").join(".") : "";
      const message = item?.msg || item?.message || "参数校验失败";
      return location ? `${location}：${message}` : message;
    }).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
  return body?.message || fallback;
}

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
    throw new Error(formatApiError(body));
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
