# BossAutoSubmit
Python + Playwright
自动投递boss消息

## 使用方式

1. 安装依赖：

```bash
pip install playwright
playwright install chromium
```

2. 第一次运行先手动登录：

```bash
python main.py --keyword "AI数据开发"
```

请在 PowerShell 或命令提示符中运行，不要直接双击 `main.py`。首次运行不要加 `--headless`，这样可以看到登录页和可能的风控提示。

3. 先预演，不实际发送：

```bash
python main.py --keyword "AI数据开发" --dry-run
```

4. 直接发送消息时可以改模板：

```bash
python main.py --keyword "AI数据开发" --message "您好，我对这个岗位很感兴趣，方便的话希望进一步了解。"
```

## 参数

- `--max-jobs`：最多处理多少个岗位
- `--delay`：每个岗位之间的等待时间
- `--headless`：无头运行
- `--reset-state`：清空已处理岗位记录

## 说明

- 脚本会复用本地登录态，保存在 `.boss_playwright_profile`
- 脚本使用系统已安装的 Google Chrome；如果未安装 Chrome，请先安装后再运行
- 已处理岗位会记录在 `boss_state.json`
- 如果页面按钮文案改了，可能需要微调选择器
- Boss 页面会检测自动化调试特征；脚本已处理常见检测并保留页面生命周期日志
