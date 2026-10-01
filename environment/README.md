# 可复现环境

S0 已在独立 Conda 环境 `finresearch-agent` 中验证，Python 实际版本为 3.11.16。

- `environment.yml`：人工维护的最小直接依赖，适合重新创建环境。
- `requirements.lock.txt`：2026-09-30 当前实际环境的完整 `pip freeze`，用于复现实测版本。

创建与验证：

```powershell
conda env create -f environment/environment.yml
conda run -n finresearch-agent python -m pytest -p no:cacheprovider tests/s0 -q
conda run -n finresearch-agent python scripts/probes/run_local_probes.py --attempt-id <new-attempt-id>
```

S0 在线探针增加了 `langchain-core` 与 `langchain-openai`，用于统一模型消息、结构化输出和工具调用。本机已经装有 S3 所需的 jieba、rank-bm25、qdrant-client 和 fastembed，本次仅把现有版本补入依赖清单，没有安装或升级。完整锁文件包含现有传递依赖；旧运行的环境证据保留在各自 attempt 中。包已安装不等于对应研究或恢复能力已通过验收。

这份锁文件固定 Python 包版本，不能单独证明干净环境重建成功。本次只在现有 Windows/Python 3.11.16 环境运行离线测试和依赖一致性检查。S3 的 Embedding 权重须单独锁定仓库 revision、实际下载文件 SHA-256 和缓存清单；模型名称不能代替权重版本。

修复期间检测到其他任务同时新增 S3/S4 代码，且本机出现 LangGraph、SQLite 检查点等依赖。完整锁文件如实记录已安装版本；本次修复没有安装这些包，也未对那些新增阶段作完成验收。`environment.yml` 的本次补充只覆盖 S3 检索直接依赖，S4 的直接依赖及干净环境重建由对应阶段另行核验。
