# RAG 分层评测数据

该目录保存版本化、完全合成的知识回答评测输入。评测不是纯函数 mock：执行器会把
`corpus.json` 通过生产知识导入 Worker 写入一个专用测试数据库，再调用生产的
ACL-first 检索、服务端引用校验、受证据约束回答和撤回服务。

## 文件

- `corpus.json`：合成文档、租户、受众、范围、版本、生命周期和可信标记。文档内容
  与 `data/fixtures/world.json` 中的合成身份配套；不包含任何真实业务资料。
- `cases.json`：公开回归集。
- `holdout_cases.json`：独立加载的流程保留集。
- `thresholds.json`：Fake 网关的机器可读门槛。
- `thresholds.deepseek.json`：DeepSeek 候选发布门槛；安全和 ACL 指标保持严格，答案
  质量门槛在真实运行前必须由模型所有者确认，不能按单次结果倒推放宽。

每个 Case 明确记录：调用身份、产品/地区/语言范围、允许文档、预期文档及版本、
禁止文档、已过期文档、关键结论，以及是否应该拒答/转人工。Case 的允许文档列表
包含正常向量兜底可能返回的同权限候选；它不是“回答一定可以引用”的许可。回答只
能使用词面命中的可信证据，这是当前产品的刻意安全边界。

## 指标

- `recall_at_k`：有预期版本的 Case 中，该精确 `document_key + version` 是否出现在
  前 k 个 ACL 过滤后的结果。
- `permission_leak_rate`：带禁止文档标注的 Case 中，是否出现任一禁止文档。
- `expired_hit_rate`：带过期文档标注的 Case 中，是否出现任一过期文档。
- `citation_correctness_rate`：非升级答案的引用是否来自当前检索、命中预期版本、未
  引用禁止文档，并在引用摘录中词面支持每个关键结论。
- `answer_key_conclusion_rate`：非升级答案是否包含标注的关键结论。
- `handoff_correctness_rate`：最终是否按标注升级；升级答案必须无引用且不含禁止结论。

`citation_correctness_rate` 的“词面支持”是可复现的回归信号，不是自然语言蕴含、
法规正确性、人工验收或真实生产召回的证明。

## 运行

默认 Fake 模式不联网：

```bash
cd apps/api
uv run eval_rag --use-default-test-database
```

该命令会迁移、清空并灌入从 `DATABASE_URL` 推导出的本机 `*_test` 数据库。它拒绝
非 loopback 主机、非 `_test` 名称和非 development/test 环境。请勿把它指向共享或
生产数据库。

固定外部评测模型需要操作者显式钉住模型，并提供与有效 provider 对应的阈值 profile：

```bash
cd apps/api
RAG_EVAL_DATABASE_URL='postgresql+psycopg://.../customer_care_test' \
uv run eval_rag --provider configured --expected-model 'approved-model-id' \
  --thresholds ../../data/evals/rag/thresholds.deepseek.json
```

报告会记录有效 provider、模型、知识回答 prompt/schema、语料和 Case/门槛指纹，但
当前不会伪造供应商 Token、费用、盲测或生产质量结论。仓库内 holdout 只与公开集
流程隔离，不是秘密、盲测或污染免疫的基准。

每次未跳过演练的执行均会发布一条故意错误的 90 天退款政策，验证发布门槛发现错误，
撤回精确版本，验证检索和回答均不再命中它，然后重跑常规 Case。详细人工处置步骤见
[`docs/runbooks/knowledge-policy-withdrawal.md`](../../../docs/runbooks/knowledge-policy-withdrawal.md)。

## Step 37 QA regression mapping

`corpus.json` 保留 `northwind-public-return-policy` 的历史 45 天 v1（`expired`），并
发布当前 30 天 v2。`rag-qa-current-return-policy-v2` 要求客户只引用 v2 且不输出
45 天结论；它与主管 QA 的知识修复目录条目绑定在 `../qa/regression-catalog.json`。
该映射只证明合成语料上的可执行本地回归，不能替代真实政策审批、独立盲测、生产 CI
记录或真实模型质量验收。
