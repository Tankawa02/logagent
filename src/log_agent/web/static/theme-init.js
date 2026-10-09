// 在首屏渲染前同步设置主题，避免暗色模式下先闪一下亮色。
// 必须是独立文件：后端 CSP 不允许内联脚本。逻辑需与 src/lib/theme.ts 保持一致。
;(function () {
  var pref = 'system'
  try {
    pref = localStorage.getItem('log-agent-theme') || 'system'
  } catch (e) {}
  var dark = pref === 'dark' || (pref !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  var root = document.documentElement
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
})()
