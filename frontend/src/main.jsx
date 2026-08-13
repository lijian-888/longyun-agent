import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { initializeAuthentication, keycloak } from "./auth";
import "./styles.css";

const root = createRoot(document.getElementById("root"));
const DataGovernanceApp = lazy(() => import("./App"));
const ResearchAssistant = lazy(() => import("./ResearchAssistant"));

function AppRouter() {
  const roles = keycloak.realmAccess?.roles || [];
  const user = {
    username: keycloak.tokenParsed?.preferred_username || "",
    display_name: [keycloak.tokenParsed?.family_name, keycloak.tokenParsed?.given_name].filter(Boolean).join(" ") || keycloak.tokenParsed?.preferred_username || "",
  };
  let page = <main className="auth-error">当前账号未配置平台角色，请联系字段管理员。</main>;
  if (roles.includes("field_admin")) page = <DataGovernanceApp user={user} accessRole="field_admin" />;
  else if (roles.includes("data_processor")) page = <DataGovernanceApp user={user} accessRole="data_processor" />;
  else if (roles.includes("researcher")) page = <ResearchAssistant />;
  return <Suspense fallback={<main className="auth-error">正在加载工作台…</main>}>{page}</Suspense>;
}

initializeAuthentication()
  .then(() => root.render(<AppRouter />))
  .catch(() => root.render(<main className="auth-error">身份认证服务不可用，请检查 Keycloak 是否已启动。</main>));
