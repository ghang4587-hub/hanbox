# 每周更新说明

网站保留现有内置样本作为兜底，并通过 GitHub Actions 每周一北京时间 09:00 更新
`data/weekly.json`。GitHub Pages 会在这个文件提交后自动重新发布，网页加载时会把
新样本和内置样本合并，因此不会因为某次抓取失败而变成空页面。

## 数据来源

`config/youtube-channels.json` 目前配置了两个频道：

- DramaBox - Stream Drama Shorts
- GoodShort Moments（页面中沿用 GoodShort 官方筛选标签）

更新任务使用 YouTube 的公开 Atom 订阅源，不需要登录，也不会把账号密码写入仓库。
订阅源提供标题、发布时间、封面、简介和当前播放量；任务还会读取新视频的公开播放页，
自动补齐真实时长，并从 YouTube 官方故事板裁出前 3 分钟的 10 张真实帧图。截图保存在
`assets/frames/<video-id>/`，已经淘汰出 30 条样本池的视频截图会自动清理。

## 可选的 Data API 增强

如需把新视频的时长补齐，可以在 GitHub 仓库的 `Settings → Secrets and variables →
Actions` 中新增仓库 Secret：

```text
YOUTUBE_API_KEY=你的 YouTube Data API v3 Key
```

API Key 只在 GitHub Actions 运行时使用，不会写进网页或提交记录。没有配置也可以正常
按周更新，脚本会从公开播放页补抓时长；配置后还能更稳定地刷新精确播放量。

## 手动测试

进入仓库的 `Actions → Weekly YouTube refresh → Run workflow` 可以立即运行一次，
无需等到下周一。成功后会产生一个 `chore: weekly YouTube refresh` 提交，Pages 随后
自动部署。
