# 日本大学外国人留学生募集要项监控

自动检查日本大学官网中的外国人留学生入试、募集要项和出愿信息，并通过 GitHub Pages 提供搜索与分类页面。

## 工作方式

- GitHub Actions 在日本时间每天 08:30–17:45 每 15 分钟运行一次
- `github_monitor.py` 检查 `schools.csv` 中启用的学校
- 首次运行只建立基准，不发送旧内容通知
- 新内容保留“新发现”标记 7 天
- GitHub Pages 发布 `web` 文件夹中的网站

## 管理学校

编辑 `schools.csv`：

- `enabled` 为 `yes`：启用监控
- `enabled` 为 `no`：停用监控
- `name`：学校名称
- `ownership`：填写 `国立`、`公立` 或 `私立`
- `url`：学校官网的招生页面

## 邮件通知

在仓库的 `Settings > Secrets and variables > Actions` 中添加：

- `EMAIL_SMTP_HOST`：例如 `smtp.gmail.com`
- `EMAIL_SMTP_PORT`：通常为 `465`
- `EMAIL_USER`：发件邮箱
- `EMAIL_PASSWORD`：邮箱应用专用密码
- `EMAIL_TO`：收件邮箱

这些信息只保存在 GitHub Secrets 中，不要写入仓库文件。

## 手动运行

打开仓库的 `Actions` 页面，选择 **Monitor universities and deploy website**，点击 **Run workflow**。
