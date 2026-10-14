# Web UI 重构：对齐 Magic Patterns 设计稿

参考设计：<https://github.com/Tankawa02/UI-Optimization-Review>（Vite + React 18 + mock 数据）。
目标：把 `web/` 的视觉与交互改成设计稿的样子，**保留现有的真实数据、路由（TanStack Router）、API 与全部功能**，不引入设计稿里的 mock 数据。

## 原则

- 只换皮和交互结构，不动 `web/src/lib/` 里的数据/业务逻辑（测试保持通过）。
- 设计稿用的是 shadcn 语义 token（`background` / `card` / `muted-foreground` / `primary` / `sidebar` …），这里在 Tailwind v4 `@theme` 里同名定义，新组件优先用语义 token。
- 现有组件大量使用 `zinc-*` / `brand-*`，把这两套色阶重新映射到设计稿的 oklch 色值，旧代码不改类名也能和新设计一致。
- 字体换成 Geist / Geist Mono，用 `@fontsource-variable` 本地打包（后端 CSP 不允许加载外部字体）。

## 任务

- [x] 0. 拉取参考仓库、跑起来截图对照（参考仓库缺几个 shadcn 基础组件，本地临时补桩运行）
- [ ] 1. 设计基础：语义 token（亮/暗）、zinc/brand 色阶重映射、Geist 字体、基础样式（滚动条、选中色、markdown）
- [ ] 2. 应用外壳：
  - 侧边栏：品牌图标、实心主色「新建分析」按钮 + 快捷键、功能导航（执行记录/技能/记忆/经验）、
    带边框的搜索框、会话分组 + 状态圆点 + 「日志 · 时间」副标题 + 「…」下拉菜单（置顶/重命名/删除）、
    底栏「设置 + 连接状态 + 主题切换」
  - 折叠态图标栏、移动端抽屉
  - 新增 `TopBar` / `PageScaffold`：每页顶部 h-14 标题栏
- [ ] 3. 新建分析页：大标题 + 输入框卡片（问题 / 日志与源码两栏 / 模型与发送）+ 快捷场景卡片
- [ ] 4. 会话工作台：顶部标题栏（标题可改名、状态、来源时间、报告/源码/时间线切换、更多、分享），对话区与右侧面板样式
- [ ] 5. 执行记录：统计卡片、筛选标签、表格；详情页面包屑 + 指标条 + 调用树/详情两栏
- [ ] 6. 技能 / 记忆 / 经验 / 设置：顶部栏 + 设计稿布局（设置页左侧分区导航 + 两列表单行）
- [ ] 7. 验证：`npm run typecheck`、`npm run lint`、`npm test`、浏览器逐页检查亮/暗色
