# BossAutoSubmit
Python + Playwright
自动投递boss消息

## 使用方式

python main.py --keyword "数据开发" --send --salary-min 15 --max-jobs 1

1. 安装依赖：

```bash
pip install playwright
```

脚本默认使用 Playwright Firefox，首次运行需要在 Firefox 中手动登录。

2. 第一次运行先手动登录并预演：

```bash
python main.py --keyword "数据开发" --dry-run --max-jobs 5
```

按薪资筛选，例如 15K 到 25K：

```bash
python main.py --keyword "数据开发" --salary-min 15 --salary-max 25 --dry-run
```

多账号登录态分开、投递记录共享：

```bash
python main.py --keyword "数据开发" --dry-run
python main.py --keyword "数据开发" --profile account_b --dry-run
```

请在 PowerShell 或命令提示符中运行，不要直接双击 `main.py`。首次运行不要加 `--headless`，这样可以看到登录页和可能的风控提示。

3. 确认职位识别无误后，再执行真实投递：

```bash
python main.py --keyword "AI数据开发" --send --max-jobs 5
```

## 参数

- `--max-jobs`：目标成功投递数量；已成功投递的重复职位不占用数量
- `--salary-min`：最低薪资，单位 K
- `--salary-max`：最高薪资，单位 K
- `--profile`：登录态名称，默认 `account_a`；新增账号可用 `account_b`，投递记录仍共用 `boss_state.json`
- `--delay`：每个岗位之间的等待时间
- `--send`：实际查找并点击“立即沟通”，不加此参数时只预演
- `--dry-run`：强制预演，即使同时传入 `--send` 也不会点击“立即沟通”
- `--reset-state`：清空已处理岗位记录

### 城市代码 
全国	100010000
北京	101010100
上海	101020100
广州	101280100
深圳	101280600
惠州	101280300
东莞	101281600
杭州	101210100
成都	101270100



## 说明

- 脚本会复用本地登录态；默认账号保存在 `.boss_profiles/account_a`，传入 `--profile account_b` 时保存在 `.boss_profiles/account_b`
- 脚本使用 Playwright Firefox 的持久化 Profile
- 找到并点击“立即沟通”就计为投递成功，不发送招呼语
- 已成功投递岗位会记录在 `boss_state.json`，预演结果不会占用真实投递名额
- 多个 `--profile` 账号共用同一个 `boss_state.json`，避免不同账号重复投递同一职位
- 每个职位会先打开详情页；默认只识别职位，只有传入 `--send` 才会执行投递
- 保留搜索标签页，详情在独立标签页处理；只滚动职位所在容器加载结果。逐批保存职位，按职位链接判断新增，不依赖卡片数量增长
- 薪资不匹配、历史重复不会终止加载。目标达到或连续等待未发现新职位时停止；等待超时不代表已确认网站没有更多职位
- 启用薪资筛选后，只保留与目标薪资范围有交集的职位；没有明确薪资的职位会跳过
- 投递入口和按钮文案可能随页面版本变化，识别不到时会记录提示而不会强行点击
- 如果出现 `code=37` 安全验证，请先在 Firefox 中完成验证，确认职位列表可访问后再重试
- 如果页面按钮文案改了，可能需要微调选择器
- Boss 页面会检测自动化调试特征；脚本已处理常见检测并保留页面生命周期日志
