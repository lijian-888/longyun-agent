# 隆耘后端 AI 网关

所有浏览器科研会话和 ACPs Partner 模型调用均由后端 AI 网关接收。浏览器只提交业务请求和 Keycloak 访问令牌，不接收上游模型密钥。

## 执行与隔离

- 每个任务在 `ai_gateway_task` 中持久化，包含机构、课题、用户/ACPs 调用方、会话、模型、状态、幂等键和时间；不在任务表保存提示词正文。
- `ai_gateway_audit` 记录排队、开始、重试、取消、完成、失败和进程中断事件。
- 单个 API 进程固定提供总计 4 个模型执行槽；生产 Compose 只运行一个 Uvicorn 进程。超过 4 个的任务保持 `queued`。
- 服务启动时，之前仍为 `queued` 或 `running` 的任务会明确标记为 `failed / worker_interrupted`，客户端可使用原幂等键安全重试。
- `POST /api/ai/tasks/{task_id}/cancel` 可取消本人任务；字段管理员可在本机构、当前课题内取消任务。
- `GET /api/ai/audit` 可按 `task_id`、`user_id`、`model`、`status`、`started_from`、`started_to` 查询。普通用户只能看到本人记录，字段管理员可看本机构当前课题记录。

## 出站安全

- 外部模型只接收公开文本或脱敏后的文本。手机号、邮箱和身份证号会替换为脱敏标记。
- 凭据、私钥、机密/不得外传标记以及服务器实际密钥会在请求发出前阻断。
- 私人附件、私人知识库片段和私人图片不能发往外部模型。需要分析此类材料时应切换到部署边界内的本地 vLLM。
- 日志过滤器会再次遮蔽模型、Tavily、MinIO 和数据库凭据。

## 模型提供方

默认配置：

```dotenv
AI_PROVIDER=shennong
AI_TASK_TIMEOUT_SECONDS=180
AI_TASK_QUEUE_WAIT_SECONDS=300
AI_TASK_MAX_ATTEMPTS=2
```

切换本地 OpenAI-compatible vLLM 时无需改业务代码：

```dotenv
AI_PROVIDER=vllm
VLLM_API_BASE_URL=http://vllm:8000/v1
VLLM_MODEL=local-model
VLLM_API_KEY=
```

切换前应先确认 vLLM 服务可从 API 容器访问；`VLLM_API_KEY` 仅在 vLLM 前置代理要求鉴权时填写。
