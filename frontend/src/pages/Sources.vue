<script setup lang="ts">
import { computed, ref } from 'vue'
import { activeTasks, state, useAction } from '../api'
const showForm = ref(false)
const search = ref('')
const form = ref({ url:'', start_position:'', concurrency:3 })
const { action, run } = useAction()
const sources = computed(() => state.sources.filter(s => `${s.title} ${s.site_name}`.toLowerCase().includes(search.value.toLowerCase())))
const completed = computed(() => state.sources.reduce((n, s) => n+s.completed, 0))
const failed = computed(() => state.sources.reduce((n, s) => n+s.failed, 0))
async function add() { await run({ kind:'download', url:form.value.url, start_position:form.value.start_position ? Number(form.value.start_position) : null, concurrency:form.value.concurrency }); if (!action.error) showForm.value = false }
</script>
<template>
  <div class="page-heading"><div><h1>来源书库</h1></div><button class="button primary" @click="showForm = !showForm">{{ showForm ? '收起表单' : '＋ 添加来源' }}</button></div>
  <div class="stats"><article><span>书库来源</span><strong>{{ state.sources.length.toString().padStart(2,'0') }}</strong></article><article><span>已保存章节</span><strong>{{ completed.toLocaleString() }}</strong></article><article><span>进行中的任务</span><strong>{{ activeTasks.length.toString().padStart(2,'0') }}</strong><small v-if="failed">{{ failed }} 章失败，可通过续传重试</small></article></div>
  <p v-if="action.error" class="message error" role="alert">{{ action.error }}</p><p v-if="action.success" class="message success" role="status">{{ action.success }} <RouterLink to="/tasks">查看任务 →</RouterLink></p>
  <form v-if="showForm" class="panel form-panel" @submit.prevent="add"><h2>添加小说来源</h2><label>小说目录 URL<input v-model="form.url" type="url" required placeholder="https://…/book/" autofocus></label><div class="form-row"><label>起始目录位置（可选）<input v-model="form.start_position" type="number" min="1" placeholder="从第一章开始"></label><label>本次章节并发<input v-model.number="form.concurrency" type="number" min="1" max="5" required></label></div><p class="hint">添加后会同步目录并下载未完成章节。默认并发 3、批间等待 10 秒。</p><p v-if="form.start_position" class="hint warning">本次统计、下载及自动导出均从第 {{ form.start_position }} 章开始，局部 TXT 将替换该来源原有 TXT。</p><details><summary>支持的站点</summary><p class="hint">{{ state.info?.sites.flatMap(s => s.domains).join(' · ') }}</p></details><div class="form-actions"><button class="button primary" :disabled="action.busy || !state.connected">{{ action.busy ? '提交中…' : '创建下载任务' }}</button></div></form>
  <section class="panel"><div class="panel-heading"><div><h2>全部来源 <span class="count">{{ sources.length }}</span></h2></div><input v-model="search" class="search" aria-label="搜索来源" placeholder="搜索书名或站点…"></div>
    <div v-if="!state.loaded && state.connected" class="empty">正在读取本地书库…</div>
    <div v-else-if="!state.loaded" class="empty" role="status">尚未读取书库，请恢复本地服务连接。</div>
    <div v-else-if="!sources.length" class="empty"><div class="empty-symbol">▤</div><h3>{{ search ? '没有匹配的来源' : '暂无来源' }}</h3><p>{{ search ? '试试其他书名或站点。' : '添加目录 URL 开始下载。' }}</p><button v-if="!search" class="button" @click="showForm = true">添加来源 →</button></div>
    <div v-else class="table-wrap"><table><thead><tr><th>小说 / 来源</th><th>保存进度</th><th>章节状态</th><th class="right">操作</th></tr></thead><tbody><tr v-for="source in sources" :key="source.id"><td><RouterLink :to="`/sources/${source.id}`" class="book-link"><span class="book-spine">{{ source.title.slice(0,1) }}</span><span><strong>{{ source.title }}</strong><small>{{ source.site_name }} <span class="muted">· #{{ source.id }}</span></small></span></RouterLink></td><td><div class="progress-label"><span>{{ source.completed.toLocaleString() }} / {{ source.total.toLocaleString() }}</span><b>{{ source.total ? Math.round(source.completed/source.total*100) : 0 }}%</b></div><progress :value="source.completed" :max="source.total || 1"></progress></td><td><span v-if="source.failed" class="badge failed">{{ source.failed }} 失败</span><span v-else-if="source.pending" class="badge pending">{{ source.pending }} 待下载</span><span v-else class="badge completed">已全部保存</span></td><td class="right"><RouterLink class="text-button" :to="`/sources/${source.id}`">管理章节 →</RouterLink></td></tr></tbody></table></div>
  </section>
</template>
