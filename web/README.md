# Sales V2 手机工作台（合成数据演示）

此目录是手机端交互演示，不连接真实 CRM、微信、Firebase 或推送。不要在本页录入真实学生信息。

浏览器内 DemoAdapter 仍是默认模式。在此目录运行 `python3 -m http.server 8765 --bind 127.0.0.1`，打开 `http://127.0.0.1:8765/`。此模式只有当前页面内存，刷新即清空。

若要演练 Python V2 离线适配器，在仓库根目录运行 `python3 web/bridge_server.py 8765`，打开 `http://127.0.0.1:8765/?bridge=offline`。页面“我的”中可切换两种模式。桥接服务只监听 `127.0.0.1`、同源提供静态页面和 JSON 路由，并拒绝非本地 Host/Origin；默认数据仅在 Python 进程内存中，服务重启即清空。可显式传入 `--sqlite-file /绝对路径/合成演示.sqlite3`，将合成事件保存在本机并于下次使用同一路径恢复；该文件并非真实客户数据库，也没有生产认证。请勿从其他设备、局域网或公开域名使用。

桥接只接受标记为 `demo_only` 的合成输入，拒绝联系人或身份字段。浏览器只提交学生消息和人工操作；默认决策输入是服务端固定合成脚本，经 `OfflineWorkspaceApi` 的 V2 Pipeline 与 Gate 处理。若要明确试验本机模型，请预先在 Ollama 安装模型，再启动 `python3 web/bridge_server.py 8765 --ollama-model qwen3.5:9b`（可用 `--ollama-timeout 240` 调整秒数）。该模式只请求固定的 `http://127.0.0.1:11434/api/chat`，使用同一 Pipeline、Gate 和人工审核链；模型调用或校验失败不会变成脚本成功。可能需要数分钟。`APPROVAL_REQUIRED` 会显示内部定制提案供合成人工审批，`STOPPED` 会显示停止原因；`HANDOFF` 或 Gate 失败会显示错误并刷新已记录的工作台事件。浏览器不能上传任意模型回答。页面展示来源事件 ID 和草稿审核状态；批准草稿后仍不会发送消息。只有勾选“已在外部渠道人工发送”并录入实际原文，才会在本地记录这一人工断言。该断言没有外部渠道核验。

运行 `node --test web/tests/*.test.js`、`python3 -m unittest web.tests.test_bridge_server -v` 检查合成流程和边界。

`api.js` 的 `SalesWorkspaceApi` 列出了接口方法；`DemoAdapter` 是纯页面实现，`bridge_api.js` 只映射本地 Python 离线结果。默认浏览器与脚本模式不调用模型，模拟决策不代表真实 Decision Agent 的输出；显式启用本机模型后也只得到合成实验输出，尚未通过业务 Eval。真实接入仍需要身份认证、学生权限校验、受保护的生产事件存储和独立验收。

前端没有向学生发送消息的能力。“标记已发送”仅在销售人员已经通过外部渠道发送之后，录入实际发送的原文和时间。未获批的定制 Offer 不会生成可审批的对客草稿。手机推送属于后续里程碑，本演示只有站内待办列表。
