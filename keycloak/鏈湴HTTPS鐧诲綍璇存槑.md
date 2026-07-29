# 本地 Keycloak HTTPS 登录

本地浏览器登录使用 `https://localhost:8443`，避免 Keycloak 的安全 OIDC Cookie 在 HTTP 回调流程中丢失。

- 证书仅用于当前电脑上的开发环境，位于 `keycloak/certs/`，已被 Git 忽略。
- 用于 `localhost` 的开发证书仅加入当前 Windows 用户的受信任证书存储，不会影响其他 Windows 账户或线上环境。
- 该证书仅用于本机演示，不能复制到正式环境；正式部署必须替换为农科院或受信任 CA 签发的 HTTPS 证书。
- API 对外校验的 issuer 为 `https://localhost:8443/realms/rice-research`；容器内部仍通过 HTTP 地址读取 Keycloak 的 JWKS 公钥。
- 若迁移到其他开发电脑，重新执行本项目的本地 HTTPS 初始化步骤即可生成该电脑自己的证书。正式部署应使用农科院签发或受信任 CA 签发的 HTTPS 证书。
