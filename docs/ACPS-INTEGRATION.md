# 隆耘智能体 ACPs v2.1 接入说明

本项目已经加入 ACPs v2.1 双角色适配层。同一套隆耘业务代码可通过 `ACPS_ROLE` 运行成：

- `partner`：通过 Direct AIP JSON-RPC 接受 Leader 的任务；
- `leader`：通过 Discovery 找 Partner，再按 AIP 调用；
- `hybrid`：同时启用上述两种角色，适合本项目当前部署。

当前实现有意只声明 Direct RPC，不声明 RabbitMQ Group 能力。等实际部署并配置 ACPs MQ Inbox 后，才能在 ACS 中增加 AMQP endpoint 和 `messageQueue`，避免能力声明与真实运行状态不一致。

## 1. 已实现接口

| 接口 | 鉴权 | 用途 |
| --- | --- | --- |
| `GET /.well-known/acps-agent.json` | 公开 | 当前运行配置生成的 ACS 02.01 描述 |
| `GET /acps/health` | 公开 | 角色、AIC、注册和 mTLS 状态 |
| `GET /acps/info` | 公开 | 角色、接口、AIP 命令和数据边界说明 |
| `POST /acps/rpc` | 生产环境 mTLS | Partner 的 AIP JSON-RPC 入口 |
| `POST /api/acps/leader/dispatch` | Keycloak `researcher` | 让隆耘作为 Leader 发现或调用 Partner |

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
ACPS_MTLS_ENABLED=true
ACPS_REQUIRE_VERIFIED_CLIENT=true
ACPS_CERTIFICATE_DNS_NAMES=agent.example.cn
```

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

## 8. 上线验收清单

- ACS 能通过 Registry 的 ACS 02.01 校验，且 AIC 已由 Registry 写回；
- Discovery 能按“水稻、育种、性状、区域试验”等标签找到 Partner；
- 未携带有效 ACPs 客户端证书访问 9443 会在 Nginx TLS 层失败；
- 有效 Leader 证书调用 `/acps/rpc` 能完成完整 AIP 状态机；
- `/acps/health` 显示 `registered=true`、`mtls=true`；
- ACPs 任务证据只包含已发布标准数据；
- 容器、反向代理和证书续期流程已纳入运维监控。
