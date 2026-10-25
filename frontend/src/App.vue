<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { activeTasks, startPolling, state, stopPolling } from './api'
onMounted(startPolling)
onUnmounted(stopPolling)
</script>
<template>
  <div class="shell">
    <aside class="sidebar">
      <RouterLink to="/" class="brand"><span class="monogram">N<span>c</span></span><span>NovelCrawl</span></RouterLink>
      <nav aria-label="主导航">
        <RouterLink to="/" :class="{ selected: $route.path === '/' || $route.path.startsWith('/sources') }"><span class="nav-icon">▤</span>来源书库</RouterLink>
        <RouterLink to="/tasks" active-class="selected"><span class="nav-icon">◷</span>任务中心<span v-if="activeTasks.length" class="nav-count">{{ activeTasks.length }}</span></RouterLink>
        <RouterLink to="/editions" :class="{ selected: $route.path.startsWith('/editions') }"><span class="nav-icon">⊞</span>合并版本</RouterLink>
      </nav>
    </aside>
    <main>
      <header class="topbar"><span class="connection" :class="{ offline: !state.connected }"><span class="dot"></span>{{ state.connected ? '本地服务已连接' : '连接中断 · 正在重连' }}</span></header>
      <div v-if="!state.connected" class="banner error" role="alert">无法连接本地服务。已有任务的运行情况暂时未知；请检查启动终端。</div>
      <div v-if="state.info?.cleanup_failed" class="banner error" role="alert">浏览器资源清理异常，已暂停调度。请查看日志并退出服务后重试。</div>
      <div class="content"><RouterView /></div>
      <footer class="legal">仅供个人学习与研究。请确认作品授权，遵守目标站点规则和请求频率限制。</footer>
    </main>
  </div>
</template>
