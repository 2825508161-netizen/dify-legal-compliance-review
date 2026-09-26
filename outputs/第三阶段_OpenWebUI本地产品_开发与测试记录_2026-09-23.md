# 第三阶段 Open WebUI 本地产品开发与测试记录

## 结论

Mac 本地 AI 法务聊天产品已经建立并稳定运行。用户可以在网页中上传 PDF、DOCX 或 TXT 合同，选择合同类型、行业、适用法律、审核严格程度和重点风险类型，查看中文风险报告，并下载 Word 与 PDF。

当前产品处于明确标注的模拟联调模式。上传、页面、进度、结果展示、异常提示和报告下载已经真实测试；Dify 测试应用的真实 API 调用尚未测试，因为该应用目前没有发布，也没有 API Key。产品没有伪造真实 Dify 成功状态。

## 项目保护

- 开发分支：`codex/open-webui-phase3`
- 第二阶段基线提交：`5c6300e`
- 第二阶段恢复标签：`backup/phase2-tested-20260921`
- 原有正式 Dify 工作流和线上恢复副本没有覆盖、发布或删除。
- 第二阶段三个审核代码节点没有改写。
- 没有推送 GitHub。
- `.env.local`、API Key、真实合同和个人隐私文件均未进入 Git。

## 实现内容

### 本地运行环境

- Open WebUI 官方稳定版本 v0.11.3。
- Open WebUI 核心源码未修改，保留官方品牌。
- 使用 Colima、Docker CLI 和 Docker Compose 在 Mac 本地运行两个独立容器。
- 网页和网关只监听 `127.0.0.1`，没有开放公网端口。

### 产品功能

- 聊天模型入口：AI 合同审核（Dify 测试版）。
- 上传格式：PDF、DOCX、TXT。
- 上传限制：最大 30 MB，空文件和不支持格式会显示中文错误。
- 审核参数：合同类型、行业、适用法律、严格程度、重点风险类型。
- 审核进度：上传、启动审核、整理报告、完成或失败。
- 结果：中文 Markdown、风险等级、高风险数量和人工复核提醒。
- 下载：Word 和 PDF。
- 安全：Dify Key 只允许放在本机被 Git 忽略的 `.env.local` 中；日志不输出 Key。
- 恢复：停止再启动后，聊天记录和已生成报告仍可访问。

### Dify 接入映射

本地网关已按第二阶段测试工作流配置以下开始变量：

- `document`
- `declared_doc_type`
- `industry`
- `jurisdiction`
- `strictness`
- `focus_risk_types`

已支持读取以下工作流输出：

- `report_markdown`
- `report_json`
- `high_risk_count`
- `manual_review_required`
- Word/PDF 文件输出

## 新增文件

- `openwebui_contract_review/docker-compose.yml`
- `openwebui_contract_review/.env.example`
- `openwebui_contract_review/README.md`
- `openwebui_contract_review/gateway/Dockerfile`
- `openwebui_contract_review/gateway/requirements.txt`
- `openwebui_contract_review/gateway/app/dify_client.py`
- `openwebui_contract_review/gateway/app/main.py`
- `openwebui_contract_review/gateway/tests/test_gateway.py`
- `openwebui_contract_review/openwebui_functions/dify_contract_review.py`
- `openwebui_contract_review/scripts/install_function.py`
- `openwebui_contract_review/scripts/start.command`
- `openwebui_contract_review/scripts/stop.command`
- `openwebui_contract_review/scripts/status.command`

## 实际测试结果

### 自动测试

- 原合同审核代码节点回归测试：27 项全部通过。
- 本地网关和 Dify 接入层测试：25 项全部通过。
- 合计：52 项全部通过。
- 覆盖正常输入、空文件、错误格式、超大文件、异常风险计数、布尔值字符串、Dify 401/403/429/500、输出缺失、路径保护和模拟审核完成。
- Python 语法检查通过。
- Git 差异格式检查通过。
- 密钥扫描通过。

测试中有一条第三方库弃用警告，不影响运行结果。

### 页面全链路测试

使用脱敏 TXT 模拟合同完成了以下操作：

1. 打开 `http://127.0.0.1:3000`。
2. 确认“AI 合同审核（Dify 测试版）”已自动安装并选中。
3. 上传合同。
4. 选择“软件开发/服务合同”“互联网与软件”“中国大陆”“严格”，并选择合同权利义务、违约责任、数据与隐私、知识产权、跨境合规。
5. 查看中文报告、风险等级、页码待定位、需人工复核和下载链接。
6. 点击 Word 与 PDF 下载按钮。
7. 未上传文件直接审核时，页面正确提示先上传 PDF、DOCX 或 TXT。
8. 页面控制台无错误。

页面清楚显示“当前是本地模拟联调结果，不代表 Dify 已完成真实审核”。

### 报告文件测试

- Word：实际下载成功，Microsoft Word 识别为 11 页；逐页检查中文、标题、风险条目和分页均正常。
- PDF：实际下载成功，8 页；逐页渲染检查中文、标题、风险条目和分页均正常。
- 停止并重新启动全部容器后，原 Word/PDF 下载地址仍可访问。
- 标准 LibreOffice 测试环境没有中文字体，渲染 Word 时出现缺字；同一文件在 macOS 快速预览和 Microsoft Word 中中文正常，因此属于测试环境字体限制，不是文件损坏。

### 浏览器测试

- Codex 内置浏览器：完整上传、选项、结果和下载测试通过。
- Safari：本地网页正常打开，中文和布局正常，合同审核模型可见，附件菜单可以展开。
- Chrome：Chrome 152 已安装且运行，但没有 ChatGPT 浏览器扩展和本机通信组件，无法进行可靠的自动化兼容性测试。此项明确标记为未验证，没有用其他浏览器结果冒充 Chrome 结果。

### 重启测试

- 一键停止成功。
- 一键重新启动成功。
- 两个容器恢复健康状态。
- 合同审核入口自动重新安装并启用。
- 聊天记录、模拟报告和下载文件保留。

## 当前未完成事项

真实 Dify 接入尚未完成最终验证。原因如下：

1. 独立 Dify 测试应用目前显示“尚未发布任何版本”。
2. 未发布应用的 API 页面不可用。
3. 当前没有测试应用 API Key。
4. 发布测试应用会生成可访问的 Dify Web App 地址，这与“不开放公网访问”的要求存在潜在冲突。
5. 创建 API Key 属于创建长期访问凭证，必须在操作前取得用户明确确认。

因此，当前不能声称已经真正连接 Dify。

## 下一步最少操作

在用户明确授权后，仅需执行以下操作：

1. 发布第二阶段的独立 Dify 测试应用，不触碰正式应用。
2. 为测试应用创建一个 API Key。
3. 将 Key 仅写入本机 `.env.local`，切换为 `DIFY_MODE=live`。
4. 重新启动本地产品。
5. 使用脱敏合同完成真实 Dify 调用、Markdown、Word 和 PDF 下载验证。
6. 如测试失败，撤销测试 Key并将本地模式恢复为 `mock`。

## 打开与停止

- 打开：双击 `openwebui_contract_review/scripts/start.command`，然后访问 `http://127.0.0.1:3000`。
- 停止：双击 `openwebui_contract_review/scripts/stop.command`。
- 查看状态：双击 `openwebui_contract_review/scripts/status.command`。

## 回滚方法

如需回到第二阶段本地基线，可从标签 `backup/phase2-tested-20260921` 恢复。执行回滚前应先保存第三阶段分支，不要覆盖现有正式 Dify 工作流。容器停止不会删除聊天记录和报告数据。
