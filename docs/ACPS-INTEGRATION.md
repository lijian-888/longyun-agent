# 隆耘智能体 ACPs v2.1 Inbox/Group 接入说明

隆耘只使用 ACPs Inbox/Group 模式。生产任务通过 RabbitMQ 交换
`TaskCommand` 和 `TaskResult`，不声明或开放 Direct JSON-RPC，也不开放 9443 入站端口。

同一套隆耘 Web、API、数据库和文件存储承载两个独立注册身份：

| 身份 | 作用 |
| --- | --- |
| Leader AIC | Discovery 选取 Partner、创建 Group/ACL、Inbox 邀请、分发和汇总任务 |
| Partner AIC | 常驻监听自己的 Inbox、加入 Group、执行隆耘两项只读能力、广播结果 |

两个 AIC 分别提交 ACS、分别申请 clientAuth 证书，但不部署两套业务系统。

## 1. 平台基线

- ACPs-community 基线：`v2.1.0-3-g5bfba5e`
- `acps-sdk==2.1.0`
- RabbitMQ 4.2.9，TLS 端口 5671，vhost `acps`
- 生产 Agent 认证：TLS 1.3、SASL EXTERNAL、ACPs clientAuth 证书
- Inbox Exchange：`inbox.topic`
- Inbox Queue：`inbox_<AIC>`
- Group Exchange：`group_<LeaderAIC>_<groupId>`
- Group Queue：`group_<LeaderAIC>_<groupId>_<MemberAIC>`
- Group Exchange 类型：`fanout`，Routing Key 为空
- Leader 通过 MQ Auth Group API（9007，mTLS）管理成员和 ACL

Group SDK 自动创建和释放 Exchange、Queue 与 Group ACL。Partner 被移除后，平台可以强制
断开该 AIC 的 RabbitMQ 连接。

## 2. 调用链

### 2.1 隆耘作为 Partner

```text
外部 Leader -> inbox_<LongyunPartnerAIC> 发送邀请
隆耘 Partner -> 校验 Inbox 邀请并加入 Group
外部 Leader -> Group Exchange 发布 TaskCommand
隆耘 Partner -> 只读分析已发布标准数据
隆耘 Partner -> Group Exchange 广播 TaskResult / Product
```

Partner 实现 `start`、`get`、`continue`、`complete` 和 `cancel`。任务状态遵循：

```text
start -> accepted -> working -> awaiting-input / awaiting-completion
      -> completed / failed / rejected / canceled
```

### 2.2 隆耘作为 Leader

```text
隆耘科研人员 -> POST /api/acps/leader/group/dispatch
隆耘 Leader -> Discovery 查询声明 RabbitMQ 能力的 Partner ACS
隆耘 Leader -> MQ Auth 创建 Group 和成员 ACL
隆耘 Leader -> Partner Inbox 发送邀请
隆耘 Leader -> Group Exchange 分发任务并汇总结果
隆耘 Leader -> 完成、取消或解散 Group
```

浏览器用户仍通过 Keycloak 登录隆耘。外部 AIC 不使用 Keycloak 账号密码；其身份由 AIC、
clientAuth 证书、SASL EXTERNAL 和 Group ACL 认证。

## 3. 两份 ACS

- `deploy/acps/acs.leader.json`：Leader AIC 草稿，不声明业务 Skill。
- `deploy/acps/acs.partner.json`：Partner AIC 草稿，只声明 AMQP Inbox 和以下两项能力：
  - `longyun.rice.published-data-analysis`
  - `longyun.rice.breeding-research`

两份 ACS 的 endpoint 都只有：

```text
amqps://<正式MQ域名>:5671/acps?inbox=inbox_<对应AIC>
```

正式提交前必须替换：

1. Registry 分配的对应 AIC；
2. 正式 RabbitMQ DNS；
3. `e-farmer.cn` 的 ICP 备案号；
4. provider license 或服务条款；
5. `lastModifiedTime`。

`capabilities.messageQueue` 保留官方值 `rabbitmq:>=4.2`。SDK 2.1.0 的 Python ACS
枚举尚未同步该表达式，代码只对这个字段使用兼容副本校验，提交的原始 ACS 不降级。

## 4. Registry、AIC 和证书

分别保存并提交两份 ACS：

```powershell
acps-cli --config acps-cli.toml agent save --acs-file deploy/acps/acs.leader.json
acps-cli --config acps-cli.toml agent submit --agent-id <LEADER_AGENT_UUID>

acps-cli --config acps-cli.toml agent save --acs-file deploy/acps/acs.partner.json
acps-cli --config acps-cli.toml agent submit --agent-id <PARTNER_AGENT_UUID>
```

审核通过后分别同步 AIC，获取各自 EAB，并分别申请 clientAuth 证书：

```powershell
acps-cli cert eab fetch --aic <LEADER_AIC> --output private/leader-eab.json
acps-cli cert issue --aic <LEADER_AIC> --eab-file private/leader-eab.json `
  --usage clientAuth --key-path leader-client-key.pem `
  --cert-path leader-client.pem --trust-bundle-path trust-bundle.pem

acps-cli cert eab fetch --aic <PARTNER_AIC> --output private/partner-eab.json
acps-cli cert issue --aic <PARTNER_AIC> --eab-file private/partner-eab.json `
  --usage clientAuth --key-path partner-client-key.pem `
  --cert-path partner-client.pem --trust-bundle-path trust-bundle.pem
```

纯 Inbox/Group 模式不申请隆耘 serverAuth 证书。EAB、私钥、Registry Token 和密码不得进入
代码仓库或日志。

运行文件安装到：

```text
deploy/acps/runtime/
  leader-client.pem
  leader-client-key.pem
  partner-client.pem
  partner-client-key.pem
  trust-bundle.pem
```

## 5. 生产配置

复制 `deploy/.env.production.example` 后填写：

```dotenv
ACPS_ENABLED=true
ACPS_ROLE=hybrid
ACPS_TRANSPORT=group
ACPS_LEADER_AIC=<LEADER_AIC>
ACPS_PARTNER_AIC=<PARTNER_AIC>

ACPS_DISCOVERY_BASE_URL=http://<正式ACPS网关>:9000/discovery
ACPS_RABBITMQ_HOST=<正式MQ域名>
ACPS_RABBITMQ_PORT=5671
ACPS_RABBITMQ_VHOST=acps
ACPS_RABBITMQ_USER=
ACPS_RABBITMQ_PASSWORD=
ACPS_ALLOW_PLAIN_RABBITMQ=false
ACPS_RABBITMQ_AUTH_SERVICE_URL=https://<正式MQ-Auth域名>:9007

ACPS_MTLS_ENABLED=true
ACPS_LEADER_CLIENT_CERT_FILE=/data/acps/leader-client.pem
ACPS_LEADER_CLIENT_KEY_FILE=/data/acps/leader-client-key.pem
ACPS_PARTNER_CLIENT_CERT_FILE=/data/acps/partner-client.pem
ACPS_PARTNER_CLIENT_KEY_FILE=/data/acps/partner-client-key.pem
ACPS_TRUST_BUNDLE_FILE=/data/acps/trust-bundle.pem
ACPS_SESSION_OWNER_USERNAME=acps.researcher
ACPS_SESSION_OWNER_SUBJECT=<Keycloak 中 acps.researcher 的 user UUID/sub>
```

当两个 AIC 相同、任一 AIC/证书缺失、MQ Auth 缺失或生产配置出现 PLAIN 用户名密码时，
`/acps/health` 会报告配置错误，Partner Inbox 不会启动，Leader 也不能创建 Group。

## 6. 网络边界

隆耘不需要向 ACPs 平台开放入站业务端口。服务器只需要出站访问：

| 目标 | 端口 | 用途 |
| --- | ---: | --- |
| ACPs 网关 | 9000 | Registry、CA、Discovery |
| RabbitMQ | 5671 | Leader/Partner Group 消息 |
| MQ Auth Group API | 9007 | Leader 创建成员和 ACL |
| DNS | 53 | 内部域名解析 |
| NTP | 123 | 证书和消息时间校验 |

平台必须根据隆耘服务器实际出站源IP进行白名单放行。RabbitMQ SNI 必须与服务端证书 DNS
SAN 一致。

## 7. 数据、私人会话和文件

Partner始终只读取已发布标准数据，不读取私人知识库、浏览器历史、附件或未发布课题数据，
也不执行发布、字段变更、治理审批和业务写入。

每个外部 Group Task 执行完成后，隆耘把问题、回答、证据和 Group 元数据保存到
`acps.researcher` 的独立私人会话，并写入 `acps_partner_result_saved` 审计记录。生产环境建议将
Keycloak 用户详情页中的用户 UUID 配置到 `ACPS_SESSION_OWNER_SUBJECT`；如果不配置，该账号
必须先登录隆耘一次以完成身份绑定。外部 AIC 不会获得该账号密码。

协议层支持 `TextDataItem`、`StructuredDataItem` 和 `FileDataItem`。PDF、Excel、PNG、JPEG
等文件必须先保存到受控存储，再通过带权限和有效期的下载 URL 返回；不得把大型文件以
Base64 塞入 RabbitMQ。每个 FileDataItem 应包含文件名、MIME、大小和 SHA-256。

## 8. 本地验证

```powershell
cd backend
python -m unittest tests.test_acps_adapter tests.test_acps_group -v
```

验收至少覆盖：

1. 两份 ACS 审批并分别回写 AIC；
2. 两张 clientAuth 证书 CN/SAN URI 与对应 AIC 一致；
3. Discovery 能按 AIC 和两项 Skill 发现隆耘；
4. Partner Inbox consumer 为1；
5. Leader创建Group和ACL，Partner加入并广播状态；
6. `start/get/continue/complete/cancel` 和终态幂等；
7. Partner退出、Leader移除、Group解散及资源/ACL释放；
8. 错误证书、错误AIC、过期邀请、跨Group访问被拒绝；
9. RabbitMQ中断重连、消息ACK和业务幂等；
10. ACPs结果保存到 `acps.researcher` 私人会话；
11. 日志包含 requestId、messageId、groupId、sessionId、taskId 和双方AIC；
12. 生产环境没有9443、Direct JSON-RPC和RabbitMQ PLAIN Agent凭据。

## 9. 平台仍需提供

正式联调前仍需取得：

- 正式 ACPs 网关、RabbitMQ 和 MQ Auth DNS；
- 隆耘 Registry账号；
- 独立 Leader AIC 和 Partner AIC；
- 两份 EAB、两张 clientAuth 证书及 trust bundle；
- 隆耘服务器源IP白名单；
- 可授权使用的测试 Leader/Partner AIC；
- 正式联调联系人、问题群和升级负责人；
- 每AIC最大连接数、Group数、Partner数和消息速率策略。
