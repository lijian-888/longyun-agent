# 隆耘智能体 ACPs v2.1 接入说明

本项目已经加入 ACPs v2.1 双角色适配层。同一套隆耘业务代码可通过 `ACPS_ROLE` 运行成：

- `partner`：通过 Direct AIP JSON-RPC 接受 Leader 的任务；
- `leader`：通过 Discovery 找 Partner，再按 AIP 调用；
- `hybrid`：同时启用上述两种角色，适合本项目当前部署。

当前代码同时包含 Direct Leader 与 Group Leader。Group 默认关闭；只有正式 AIC、RabbitMQ、mq-auth 和认证材料全部就绪时，`/acps/health` 才会显示 `groupLeader.ready=true`，ACS 才声明 `amqp:0.9.1`，避免能力声明与真实运行状态不一致。当前 Group 会话保存在单个 API 进程内，服务重启后必须重新建组。

> 版本说明：AIP-PUB 于 2026-09-03 发布了仓库标签 `v2.2.0`，但截至同日 PyPI 可安装的 `acps-sdk` 最新版本仍为 `2.1.0`，本项目和现有 214 环境也固定在 `2.1.0`。v2.2 教程中的 Direct/Group Leader 高层接口与这里使用的接口保持一致，因此先完成核心 Leader 能力；流式身份绑定、AMP、AAC、OIDC 与 Ed25519 迁移必须等平台侧 v2.2 部署资料和可固定校验的 SDK 制品到位后另行验收，当前不得宣称“完整通过 ACPs v2.2”。

## 1. 已实现接口

| 接口 | 鉴权 | 用途 |
| --- | --- | --- |
| `GET /.well-known/acps-agent.json` | 公开 | 当前运行配置生成的 ACS 02.01 描述 |
| `GET /acps/health` | 公开 | 角色、AIC、注册和 mTLS 状态 |
| `GET /acps/info` | 公开 | 角色、接口、AIP 命令和数据边界说明 |
| `POST /acps/rpc` | 生产环境 mTLS | Partner 的 AIP JSON-RPC 入口 |
| `POST /api/acps/leader/dispatch` | Keycloak `researcher` | 让隆耘作为 Leader 发现或调用 Partner |
| `POST /api/acps/leader/tasks/{task_id}/commands` | Keycloak `researcher` | 控制本人在当前课题创建的 Direct 任务 |
| `POST /api/acps/leader/groups` | Keycloak `researcher` | 创建本人在当前课题内的 Group 会话 |
| `GET/DELETE /api/acps/leader/groups/{session_id}` | Keycloak `researcher` | 查询或解散 Group 会话 |
| `POST /api/acps/leader/groups/{session_id}/partners` | Keycloak `researcher` | 通过 Inbox 或 Direct RPC 邀请 Partner |
| `POST /api/acps/leader/groups/{session_id}/tasks` | Keycloak `researcher` | 向全组或指定 Partner 发送任务消息 |
| `POST /api/acps/leader/groups/{session_id}/tasks/{task_id}/commands` | Keycloak `researcher` | 继续、完成或取消 Group 任务 |
| `POST /api/acps/leader/groups/{session_id}/partners/{partner_aic}/commands` | Keycloak `researcher` | 查询、静音、移除或强制移除成员 |

Partner 实现 `start`、`get`、`continue`、`complete`、`cancel`。正常异步状态为：

```text
start -> accepted -> working -> awaiting-completion -> complete -> completed
```

输入为空时进入 `awaiting-input`。任务执行期间可以取消；失败时进入 `failed`。当前 SDK 的 `TaskManager` 是进程内存存储，因此容器重启会丢失未完成任务。若要承接长时间生产任务，应再把任务状态迁移到 PostgreSQL 或 Redis。

## 2. 隆耘 Partner 的数据边界

ACPs 外部调用只进入现有的“已发布标准数据”证据链：

- 不读取科研人员的私有知识库；
- 不读取浏览器会话历史或附件；
- 不执行外部 Web 搜索；
- 不提供数据写入、发布、字段变更或治理审批能力；
- 输出同时包含文本 `Product` 和只读证据摘要 `StructuredDataItem`。

这使 ACPs Partner 的公开能力和现有单机构数据权限边界保持一致。

## 3. 本地验证

本地 `docker-compose.yml` 默认使用 `ACPS_ROLE=hybrid`、明文 Direct RPC，仅用于开发。启动或重建后检查：

```bash
docker compose -p longyun-main-rework up -d --build api web
curl http://localhost:5183/acps/health
curl http://localhost:5183/.well-known/acps-agent.json
```

本地 ACS 的 `aic` 为空表示尚未注册。空 AIC 可以用于准备草稿，但不能作为正式互联身份。

## 4. 准备 ACS

仓库提供两份独立注册模板：

- `deploy/acps/acs.partner.json`：作为可被发现和调用的 Partner；
- `deploy/acps/acs.leader.json`：作为发起协作的 Leader。

若只注册一个实体并以 `hybrid` 运行，优先使用 Partner 模板：它包含可发现的技能和 JSON-RPC endpoint；Leader 是交互时承担的协议角色，并不要求继承另一套代码框架。若平台管理策略要求 Leader 与 Partner 使用不同身份，则分别提交两份 ACS、取得两个 AIC 和各自证书，并分成两个运行实例。

提交前必须替换模板中的：

1. 真实公网或平台可达域名；
2. 真实组织、部门、联系人和邮箱；
3. ICP 或 WHOIS 登记信息；
4. Partner endpoint 和证书 SAN；
5. `lastModifiedTime` 和业务版本。

不要手工编造 AIC。Registry 审核通过后会分配 AIC，再把服务端返回的最新 ACS 同步到本地。

## 5. Registry 注册和证书申请

`acps-cli` 当前随 ACPs-community 源码提供。按平台方给出的版本和地址安装，在私有工作目录复制 `deploy/acps/acps-cli.toml.example`，然后执行官方最小命令链：

```bash
acps-cli --config ./acps-cli.toml auth login --username <用户名> --password '<密码>'
acps-cli --config ./acps-cli.toml agent save --acs-file ./acs.partner.json --json
acps-cli --config ./acps-cli.toml agent submit --agent-id <上一步返回的AGENT_UUID> --json
acps-cli --config ./acps-cli.toml agent check --acs-file ./acs.partner.json --json
acps-cli --config ./acps-cli.toml cert eab fetch --aic <审核后获得的AIC> --output ./private/eab.json --json
acps-cli --config ./acps-cli.toml cert issue --aic <AIC> --eab-file ./private/eab.json --usage clientAuth
```

Partner 的 9443 入站还需要签发 `serverAuth` 证书。具体命令参数以所连接平台的 v2.1 CLI 为准，证书 SAN 必须与 ACS endpoint 的域名一致。最终把文件安装为：

```text
deploy/acps/runtime/
  client.pem
  client-key.pem
  server.pem
  server-key.pem
  trust-bundle.pem
```

EAB、私钥、证书和 Registry token 全部属于私密运行材料，已经通过 `.gitignore` 排除，不得提交到 GitHub。

只在宿主机运行后端测试而不使用 Docker 时，还需安装独立协议依赖：

```bash
pip install -r backend/requirements-acps.txt
```

## 6. 生产 mTLS 部署

先在 `deploy/.env.production` 填写 ACPs 配置：

```dotenv
ACPS_ENABLED=true
ACPS_ROLE=hybrid
ACPS_AIC=<Registry分配的AIC>
ACPS_PUBLIC_BASE_URL=https://agent.example.cn:8443
ACPS_RPC_URL=https://agent.example.cn:9443/acps/rpc
ACPS_DISCOVERY_BASE_URL=https://discovery.example.cn/acps-adp-v2
ACPS_REQUIRE_REGISTERED_LEADER=true
ACPS_ALLOWED_PARTNER_HOSTS=partner-a.example.cn,partner-b.example.cn
ACPS_MTLS_ENABLED=true
ACPS_REQUIRE_VERIFIED_CLIENT=true
ACPS_CERTIFICATE_DNS_NAMES=agent.example.cn
```

`ACPS_ALLOWED_PARTNER_HOSTS` 是出站 Partner 主机白名单，支持精确主机名和 `*.example.cn` 通配形式。生产环境应显式配置；Partner URL 不允许携带用户名、密码或 URL fragment。`ACPS_REQUIRE_REGISTERED_LEADER=true` 可防止未取得正式 AIC 的实例对外发起 Leader 请求。

生产 Web 仍走原来的 8443。下面的覆盖文件额外开放 9443，强制校验 ACPs 客户端证书，并用证书 Subject CN 中的 AIC 覆盖 `X-ACPS-Client-AIC`；外部请求不能直接访问 API 容器，因此无法伪造这个受信头：

```bash
docker compose \
  --env-file deploy/.env.production \
  -f docker-compose.lan.yml \
  -f deploy/acps/docker-compose.acps.yml \
  up -d --build
```

生产 ACS 不应出现 `http://` endpoint，也不应在缺少真实证书时设置 `ACPS_ENABLED=true`。服务器 `serverAuth` 证书、Leader `clientAuth` 证书和 trust bundle 必须来自所接入 ACPs 平台的 CA。

## 7. Leader 调用

科研人员登录隆耘后，可显式指定 Partner：

```json
POST /api/acps/leader/dispatch
{
  "query": "比较某候选材料与对照品种的区域试验稳定性",
  "partner_url": "https://partner.example.cn/acps/rpc",
  "partner_aic": "<PARTNER_AIC>",
  "auto_complete": true
}
```

不传 `partner_url` 时，隆耘会向 `ACPS_DISCOVERY_BASE_URL/discover` 发送 explicit discovery 请求，从候选 ACS 中选取排名最前且带 JSONRPC endpoint 的 Partner。结果停在 `awaiting-input` 时返回给调用方补充信息；停在 `awaiting-completion` 时，`auto_complete=true` 会自动确认并进入 `completed`。

Direct 任务的后续命令不再由调用方重复提交 Partner URL，而是使用服务端保存且绑定到“登录用户 + 当前课题”的任务句柄：

```json
POST /api/acps/leader/tasks/<TASK_ID>/commands
{
  "command": "continue",
  "query": "补充试验环境为海南陵水，比较三年稳定性",
  "wait_for_result": true,
  "auto_complete": true
}
```

`command` 可取 `get`、`continue`、`complete`、`cancel`。句柄默认保留 3600 秒，可通过 `ACPS_LEADER_TASK_RETENTION_SECONDS` 调整；API 进程重启后句柄失效，不会允许用户通过猜测 taskId 控制其他用户或其他课题的任务。

## 8. Group Leader

Group Leader 基于 ACPs v2.1 官方 `GroupLeader` SDK。启用前由 ACPs 平台管理员提供正式配置：

```dotenv
ACPS_GROUP_ENABLED=true
ACPS_RABBITMQ_HOST=registry-mtls.example.cn
ACPS_RABBITMQ_PORT=5671
ACPS_RABBITMQ_VHOST=acps
ACPS_GROUP_AUTH_SERVICE_URL=https://mq-auth.example.cn
ACPS_GROUP_INVITATION_TIMEOUT_SECONDS=300
```

生产环境使用 AMQPS + EXTERNAL/mTLS 时复用 `ACPS_CLIENT_CERT_FILE`、`ACPS_CLIENT_KEY_FILE` 和 `ACPS_TRUST_BUNDLE_FILE`，RabbitMQ 用户名、密码保持为空。只有开发环境明确使用账号认证时才填写 `ACPS_RABBITMQ_USER` 和 `ACPS_RABBITMQ_PASSWORD`。

最小操作顺序为：

1. `POST /api/acps/leader/groups` 创建 Group；
2. 对每个 Partner 调用 `POST .../partners`，优先提交 Registry 返回的完整 `partner_acs`，使 SDK 选择 MQ Inbox；只提供 `partner_url` 时使用官方 Direct RPC 邀请回退；
3. `POST .../tasks`，不传 `target_partners` 表示广播，传 AIC 数组表示定向发送；
4. 通过任务命令接口发送 `continue`、`complete` 或 `cancel`；
5. 通过成员命令接口发送 `status`、`mute`、`unmute`、`leave` 或 `force-remove`；
6. `DELETE /api/acps/leader/groups/{session_id}` 执行官方 SDK 的广播关闭、ACL 和队列资源清理流程。

所有 Group 会话均绑定创建者和当前课题，并记录到 `permission_audit`。当前实现适合单 API 进程的首轮联调；正式多副本部署前，需要将会话所有权和事件记录迁移到共享存储，并为同一 Group 固定路由到持有 RabbitMQ 连接的实例。

## 9. 上线验收清单

- ACS 能通过 Registry 的 ACS 02.01 校验，且 AIC 已由 Registry 写回；
- Discovery 能按“水稻、育种、性状、区域试验”等标签找到 Partner；
- 未携带有效 ACPs 客户端证书访问 9443 会在 Nginx TLS 层失败；
- 有效 Leader 证书调用 `/acps/rpc` 能完成完整 AIP 状态机；
- `/acps/health` 显示 `registered=true`、`mtls=true`；
- 启用 Group 时 `/acps/health` 显示 `groupLeader.ready=true`，并完成建组、Inbox 邀请、广播/定向消息、成员移除和解散实测；
- ACPs 任务证据只包含已发布标准数据；
- 容器、反向代理和证书续期流程已纳入运维监控。
