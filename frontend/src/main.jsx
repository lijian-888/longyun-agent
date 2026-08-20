import React, { Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { initializeAuthentication, keycloak } from "./auth";
import { request, setActiveProjectId } from "./api";
import "./styles.css";

const root = createRoot(document.getElementById("root"));
const DataGovernanceApp = lazy(() => import("./App"));
const ResearchAssistant = lazy(() => import("./ResearchAssistant"));

function AppRouter() {
  const [platformContext, setPlatformContext] = useState(null);
  const [contextError, setContextError] = useState("");
  const roles = keycloak.realmAccess?.roles || [];
  const user = {
    username: keycloak.tokenParsed?.preferred_username || "",
    display_name: [keycloak.tokenParsed?.family_name, keycloak.tokenParsed?.given_name].filter(Boolean).join(" ") || keycloak.tokenParsed?.preferred_username || "",
  };
  useEffect(() => {
    (async () => {
      try {
        let context;
        try {
          context = await request("/api/context");
        } catch (error) {
          setActiveProjectId("");
          context = await request("/api/context");
        }
        setActiveProjectId(context.active_project_id);
        setPlatformContext(context);
      } catch (error) {
        setContextError(error.message || "无法进入海南南繁工作环境。");
      }
    })();
  }, []);

  function changeProject(projectId) {
    if (!projectId || projectId === platformContext?.active_project_id) return;
    setActiveProjectId(projectId);
    window.location.reload();
  }

  if (contextError) return <main className="auth-error">{contextError}</main>;
  if (!platformContext) return <main className="auth-error">正在进入海南南繁工作环境…</main>;

  let page = <main className="auth-error">当前账号未配置三类业务角色，请联系字段管理员。</main>;
  if (roles.includes("field_admin")) page = <DataGovernanceApp user={user} accessRole="field_admin" platformContext={platformContext} onProjectChange={changeProject} />;
  else if (roles.includes("data_processor")) page = <DataGovernanceApp user={user} accessRole="data_processor" platformContext={platformContext} onProjectChange={changeProject} />;
  else if (roles.includes("researcher")) page = <ResearchAssistant platformContext={platformContext} onProjectChange={changeProject} />;
  return <Suspense fallback={<main className="auth-error">正在加载工作台…</main>}>{page}</Suspense>;
}

initializeAuthentication()
  .then(() => root.render(<AppRouter />))
  .catch(() => root.render(<main className="auth-error">身份认证服务不可用，请检查 Keycloak 是否已启动。</main>));
