# Finance-Agent / FinResearch Agent

当前 FinResearch Agent 的 Python 实现与网页工作台源码，版本日期：2026-10-01。

当前实现包括 B1 首轮研究、独立 B2 有界补查、财务计算与证据引用、SQLite 检查点和动作日志、持久化预算、本机 HTTP 服务及原生 MJS/CSS 网页。B2 显式选择已发布的 B1 作为基线；当前 writer 排列结论，程序模板生成正文。统一规划、增量结构化复判和 AI 自由成文尚未实施。

## 网页预览

在仓库根目录运行，只需 Python 标准库，无需安装前端依赖或配置模型密钥：

```powershell
python -B scripts/web/serve_preview.py --port 8765
```

打开 <http://127.0.0.1:8765/>。这是已标注的只读样本预览，不创建真实任务、不调用模型；源码上传不等于已部署公网网站。

## 后端工作台

```powershell
conda env create -f environment/environment.yml
conda activate finresearch-agent
python -B scripts/web/serve_app.py --port 8766 --live-budget 0
```

打开 <http://127.0.0.1:8766/>。默认累计预算0，关闭收费创建。真实研究还需在本机准备与源码锁匹配的年报PDF、页面快照、READY语料及验收文件，并在服务端环境配置 `DEEPSEEK_API_KEY` 和明确的费用授权。仓库不包含凭证、原始年报、页面全文、数据库、真实运行记录、索引或模型缓存；不能仅凭源码克隆宣称真实研究已可执行。已有本机费用授权不自动迁移。

## 目录

| 目录 | 内容 |
|---|---|
| `src/finresearch/` | 类型合同、财务、解析、检索、模型、存储、B1/B2编排 |
| `src/finresearch_web/` | 本机协调、控制与只读投影 |
| `scripts/` | CLI、阶段核验、评测与HTTP服务入口 |
| `web/` | 网页、合同及已标注的展示样本 |
| `configs/`、`protocols/` | 模型/检索/预算配置与财务协议 |
| `environment/` | 直接依赖与实际版本锁 |
| `tests/`、`evals/` | 测试代码和开发评测用例 |

全部业务和网页源码保持原始字节。输入锁检查依赖SHA-256，`.gitattributes` 禁止自动换行转换；变更源码或资料后需要按对应门禁重新核验，不能修改历史hash绕过检查。

## 无模型测试

无需本机年报或历史任务的测试命令：

```powershell
python -B -m pytest tests/s0/test_probe_guard.py tests/s2/test_calculations.py tests/s4/test_claims_and_writer.py tests/s4/test_persistent_budget.py tests/web/test_allocation.py tests/web/test_w0_contracts.py -k "not test_export_check_is_deterministic_without_touching_sources" -q -p no:cacheprovider
node --test tests/web/frontend_display.test.mjs tests/web/frontend_state.test.mjs tests/web/frontend_service.test.mjs tests/web/frontend_tour.test.mjs tests/web/frontend_scroll.test.mjs tests/web/frontend_layout.test.mjs tests/web/frontend_motion.test.mjs
```

上传前在独立源码目录验证：Python **106 passed，1 deselected**；前端 **120 passed**；静态主页及脚本、样式、样本资源可正常读取。Python禁用缓存插件产生1条既有`cache_dir`配置警告。排除的W0重新导出检查依赖未上传的历史数据库。其他来源/恢复集成测试仍保留，但需先准备其明确依赖的资料和run文件；“不调用模型”不等于“不需要资料”。测试与HTTP读取不能替代浏览器视觉验收或真实模型质量评测。

本仓库此次只发布实现、网页、测试、配置和依赖。完整设计规划、审计记录与来源元数据归档保留在本机，没有随此次公开上传。
