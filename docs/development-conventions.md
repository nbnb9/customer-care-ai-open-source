# CustomerCare AI 开发约定

- 状态：已采纳
- 日期：2026-08-12
- 适用范围：整个仓库

## 总体原则

- P0 采用模块化单体，不按业务域拆分微服务。
- Local 和 CI 只使用完全合成的数据。
- 密钥、Token、密码、私钥和真实客户数据不能进入仓库。
- 前端传入的 tenant、role 和 customer 标识不能直接作为权限依据。
- 业务状态、权限、幂等和审计由确定性服务端代码控制。
- 锁文件应提交，包括 `pnpm-lock.yaml` 和 `uv.lock`。
- `.gitkeep` 仅用于追踪空目录；出现真实文件后删除。

## 目录职责

| 路径 | 职责 | 不应放入 |
|---|---|---|
| `apps/api` | FastAPI 模块化单体 | 前端组件、模型供应商密钥 |
| `apps/web` | Next.js 多角色前端 | 服务端权限真相、数据库凭据 |
| `apps/worker` | 异步任务与后续 Workflow | 重复的业务规则实现 |
| `packages/contracts` | OpenAPI、JSON Schema、生成类型 | 业务逻辑 |
| `packages/prompts` | 后续 Prompt、版本及测试元数据 | API Key、真实客户内容 |
| `packages/ui` | 共享 UI 和设计令牌 | 数据库或外部系统调用 |
| `data/fixtures` | 可提交的合成数据 | 生产导出或简单脱敏的真实数据 |
| `data/evals` | 版本化评测案例和标注规范 | 未授权真实会话 |
| `docs/adr` | 架构决策记录 | 临时操作日志 |
| `docs/product` | 范围、指标和产品设计 | 密钥与运行时配置 |
| `docs/runbooks` | 启动、恢复和事故处理步骤 | 模糊的设计愿景 |
| `docs/threat-model` | 威胁、风险、控制和验证 | “已考虑安全”式空泛结论 |
| `infra/compose` | 本地和 CI 的 Compose 配置 | 生产密钥 |
| `infra/k8s` | 未来确有需要时的部署定义 | 当前 P0 的伪 Kubernetes 配置 |
| `infra/observability` | Collector、Dashboard 和告警 | 未脱敏业务数据 |
| `tests/unit` | 单个模块和纯函数测试 | 跨服务完整流程 |
| `tests/integration` | 数据库、缓存和模块集成测试 | 真实外部账户 |
| `tests/contract` | API 与外部 Mock 契约测试 | UI 视觉测试 |
| `tests/e2e` | 用户完整流程 | 大量细粒度单元案例 |
| `tests/security` | 越权、注入和敏感信息测试 | 真实攻击凭据 |
| `tests/load` | 容量与延迟测试 | 未定义目标的盲目压测 |
| `tests/evals` | 后续 AI 质量回归 | P0 业务正确性替代品 |

## 数据规则

- `data/fixtures` 中的记录必须带有 `source: synthetic`。
- 不创建 `data/prod-export` 等生产导出目录。
- `.env`、`data/private/`、`data/raw/` 和 `data/exports/` 保持 Git 忽略。
- 截图、日志和测试失败信息在分享前必须检查敏感内容。

## 变更规则

- 一个 commit 尽量只表达一个目的。
- 格式清理不与业务或架构变更混在同一 commit。
- 新增顶层目录前先说明职责。
- 修改已采纳的架构决策时新增或修订 ADR，不悄悄覆盖历史理由。
