# FinResearch本机工作台

从仓库根目录运行：

```powershell
python -B scripts/web/serve_preview.py --port 8765
```

打开 `http://127.0.0.1:8765/`，使用已标注的只读样本查看网页，不启动模型。

安装Python依赖后可运行真实服务：

```powershell
python -B scripts/web/serve_app.py --port 8766 --live-budget 0
```

默认禁止收费创建。真实B1/B2任务还需要与源码锁匹配的资料和验收产物，以及服务端凭证和费用授权，见根README。服务绑定回环地址，不是公网部署。

`app/` 保存组件、状态、动画和API适配，`styles/` 保存样式，`contracts/`与`fixtures/`保存合同及展示样本。历史样本和合成样本均保留原标签，不能作为现场执行证明。

网页源码保持当前实现。本次不自行进行浏览器视觉验收或收费演示。
