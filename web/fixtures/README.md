# W0 展示样本

此目录是页面验收数据，没有运行研究，也没有在线服务。入口为 [manifest.json](manifest.json)。schema与中文词表在 [contracts](../contracts/)，规则依据见 [接口与展示状态合同](../../docs/stages/s8/接口与展示状态合同.md)。

- history-b1、history-b2：只读派生历史发布，带源文件SHA；发布核验不等于当前源头/语义重验。
- 其余场景：明确合成执行，可能复用真实内容；必须显示样本标记，操作不能发送真实任务请求。
- 每个frames项有快照、来源、可选合成事件与预期行为。用于W1页面/状态验收，不用定时器模拟真实后台。
- content-extremes包含合成无值边界与DRAFT观察，不能用来证明财务结论。

构建与只读检查：在应用根目录使用已有Agent Python执行 scripts/web/build_w0_contracts.py；加 --check 仅验证，不写入。禁止手工修改生成JSON；需要扩展场景时修改生成器并重新校验。
