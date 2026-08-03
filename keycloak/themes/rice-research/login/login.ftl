<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${realm.displayName!"隆耘 Agent 育种智能体"}</title>
  <link rel="stylesheet" href="${url.resourcesPath}/css/login.css" />
</head>
<body class="rice-login-page">
  <main class="rice-login-shell">
    <header class="rice-login-header">
      <a class="brand" href="#" aria-label="隆耘 Agent 育种智能体">
        <span class="brand-mark" aria-hidden="true"><img src="${url.resourcesPath}/img/longyun-agent-logo.png" alt="" /></span>
        <span>
          <strong>隆耘 Agent 育种智能体</strong>
          <small>Longyun Agent · Breeding Intelligence</small>
        </span>
      </a>
      <div class="deployment-note"><span></span> 本地部署 · 研究数据受控访问</div>
    </header>

    <section class="rice-login-content" aria-label="登录与平台能力介绍">
      <div class="product-intro">
        <p class="eyebrow">面向水稻育种与试验研究</p>
        <h1>让数据沉淀为可追溯的<br />科研证据与决策依据</h1>
        <p class="intro-copy">从分散的试验表、表型记录和文献资料，到材料比较、区域试验统计、基因型质控与审定辅助报告，统一在受控的数据基础上完成。</p>

        <div class="capability-grid" aria-label="已支持能力">
          <article>
            <span class="capability-index">01</span>
            <strong>数据治理</strong>
            <p>标准模板、字段映射、质量检查、审核发布与全过程追溯。</p>
          </article>
          <article>
            <span class="capability-index">02</span>
            <strong>区域试验分析</strong>
            <p>随机区组方差分析、Tukey 比较、多年多点稳定性与环境解释。</p>
          </article>
          <article>
            <span class="capability-index">03</span>
            <strong>知识与科研问答</strong>
            <p>本地知识库、附件解析、证据卡片与公开资料检索协同使用。</p>
          </article>
          <article>
            <span class="capability-index">04</span>
            <strong>基因型质控</strong>
            <p>VCF / PLINK 导入、材料映射、水稻专用 QC 与结果包归档。</p>
          </article>
        </div>

        <div class="feature-line">
          <span>试验数据可比较</span><i></i>
          <span>结论有来源证据</span><i></i>
          <span>研究产物可归档</span>
        </div>
      </div>

      <section class="sign-in-card" aria-label="账号登录">
        <div class="sign-in-heading">
          <p>安全登录</p>
          <h2>进入科研工作台</h2>
          <span>使用院内分配账号访问已授权的数据与工具。</span>
        </div>

        <#if message?has_content>
          <div class="login-message login-message-${message.type}">${kcSanitize(message.summary)?no_esc}</div>
        </#if>

        <form id="kc-form-login" class="login-form" onsubmit="login.disabled = true; return true;" action="${url.loginAction}" method="post">
          <label for="username">账号</label>
          <input tabindex="1" id="username" name="username" value="${(login.username!'')}" type="text" autofocus autocomplete="username" required />

          <div class="password-label-row">
            <label for="password">密码</label>
            <#if realm.resetPasswordAllowed>
              <a href="${url.loginResetCredentialsUrl}">忘记密码</a>
            </#if>
          </div>
          <input tabindex="2" id="password" name="password" type="password" autocomplete="current-password" required />

          <#if realm.rememberMe && !usernameEditDisabled??>
            <label class="remember-row" for="rememberMe">
              <input tabindex="3" id="rememberMe" name="rememberMe" type="checkbox" <#if login.rememberMe??>checked</#if> />
              <span>记住本次登录状态</span>
            </label>
          </#if>

          <button tabindex="4" class="login-button" name="login" id="kc-login" type="submit">安全登录 <span aria-hidden="true">→</span></button>
        </form>

        <p class="sign-in-footnote">首次登录可能需要按院内安全策略更新密码。</p>
      </section>
    </section>
  </main>
</body>
</html>
