# 每周更新说明

网站保留现有内置样本作为兜底，并通过 GitHub Actions 每周一北京时间 09:00 更新
`data/weekly.json`。GitHub Pages 会在这个文件提交后自动重新发布，网页加载时会把
新样本和内置样本合并，因此不会因为某次抓取失败而变成空页面。

## 数据来源

`config/youtube-channels.json` 目前配置了两组可嵌入播放的数据源：

- 海外：DramaBox、GoodShort
- 国内：优酷微剧、阅文短剧、腾讯视频微短剧播放列表

国内样本使用国内平台或版权方的 YouTube 官方/正版分发内容，因此仍可在网页卡片内
直接播放。配置会过滤漫剧、动画、预告、试看、抢先看、花絮和单集 EP 等碎片内容，
保留合集、全集、完整版、Full Version 以及 EP01-xx 形式的完整剧样本。
更新频繁且混合发布宣传切片的频道会启用 `completeOnly`，只在标题明确标注完整剧时纳入。

更新任务优先使用仓库 Secret 中的 YouTube Data API，并以公开 Atom 订阅源和频道页作为
无 Key/接口异常时的兜底，不需要登录，也不会把账号密码写入仓库。数据源提供标题、发布
时间、封面、简介和当前播放量；任务还会读取新视频的公开播放页，
自动补齐真实时长，并从 YouTube 官方故事板裁出前 3 分钟的 10 张真实帧图。截图保存在
`assets/frames/<video-id>/`，已经淘汰出样本池的视频截图会自动清理。

## 选样规则

- 常驻滚动样本共 48 条，国内与海外各 24 条。
- 国内按腾讯视频 12、优酷 6、阅文短剧 6 保留平台代表性；海外按 DramaBox 12、
  GoodShort 12 分配。各平台内部再按本周新内容和播放量滚动淘汰。
- 每周最多优先纳入国内 6 条、海外 6 条新视频，并淘汰同量的低热或较旧样本。
- 页面将每个市场最值得参考的 8 条标为“本周重点”，合计 16 条。
- 两个市场独立选样，避免不同内容生态的播放量直接互相挤占名额。

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
