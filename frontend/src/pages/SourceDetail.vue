<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, state, useAction } from '../api'
import { usePagedList } from '../pagination'
import type { Chapter, Page } from '../types'
import { labels, titleOf } from '../types'
import Pagination from '../components/Pagination.vue'
import Preview from '../components/Preview.vue'
const route = useRoute()
const id = computed(() => Number(route.params.id))
const source = computed(() => state.sources.find(s => s.id === id.value))
const filter = ref(''), selected = ref<number[]>([])
const start = ref(''), concurrency = ref(3), range = ref(''), refreshMode = ref('selected')
const preview = ref<Chapter | null>(null), previewError = ref(''), previewBusy=ref(false)
const {page,size,data,total,error,busy,load} = usePagedList<Page<Chapter>>(() => `/sources/${id.value}/chapters${filter.value ? '?status='+filter.value : ''}`)
const { action, run } = useAction()
const directory=ref<HTMLElement>()
const selectedOnPage=computed(() => data.value?.items.filter(c=>selected.value.includes(c.id)).length || 0)
watch([id, filter], () => { selected.value=[] })
watch(id, () => { closePreview(); action.error=''; action.success='' })
async function scrollToDirectory() { await nextTick(); directory.value?.scrollIntoView?.({block:'start'}) }
let previewRequest: AbortController | undefined
let previewTarget: number | null=null
onUnmounted(() => previewRequest?.abort())
function closePreview() { previewRequest?.abort(); preview.value=null; previewError.value=''; previewBusy.value=false; previewTarget=null }
async function read(chapterId: number) {
  previewRequest?.abort()
  const request = new AbortController(); previewRequest=request; previewTarget=chapterId; previewError.value=''; previewBusy.value=true
  try { const result=await api<Chapter>(`/chapters/${chapterId}`, { signal:request.signal }); if (!request.signal.aborted) preview.value=result }
  catch(e) { if (!request.signal.aborted) previewError.value=(e as Error).message }
  finally { if (!request.signal.aborted) previewBusy.value=false }
}
function selectAll(event: Event) { const ids = data.value?.items.map(c => c.id) || []; selected.value = (event.target as HTMLInputElement).checked ? [...new Set([...selected.value, ...ids])] : selected.value.filter(i => !ids.includes(i)) }
function refreshChapters() { void run({ kind:'refresh', source_id:id.value, concurrency:concurrency.value, ...(refreshMode.value === 'all' ? { all:true } : refreshMode.value === 'range' ? { chapters:range.value } : { chapter_ids:selected.value }) }) }
</script>
<template>
  <RouterLink to="/" class="back-link">← 返回来源书库</RouterLink>
  <div class="page-heading"><div><span class="eyebrow">SOURCE / {{ source?.site_name || id }}</span><h1>{{ source?.title || '来源详情' }}</h1><p class="url-text">{{ source?.catalogue_url }}</p></div><button class="button" :disabled="action.busy || !state.connected" @click="run({ kind:'export_source', source_id:id })">导出完整 TXT</button></div>
  <p v-if="action.error || (!preview && previewError)" class="message error" role="alert">{{ action.error || (!preview && previewError) }}</p><p v-if="action.success" class="message success" role="status">{{ action.success }} <RouterLink to="/tasks">查看任务 →</RouterLink></p>
  <div class="detail-grid"><form class="panel form-panel" @submit.prevent="run({ kind:'resume', source_id:id, start_position:start ? Number(start) : null, concurrency })"><h2>继续下载</h2><p class="hint">重新同步目录，只下载待下载和失败章节。</p><div class="form-row"><label>起始目录位置<input v-model="start" type="number" min="1" placeholder="全部章节"></label><label>本次并发<input v-model.number="concurrency" type="number" min="1" max="5" required></label></div><p v-if="start" class="hint warning">统计、请求和自动导出从第 {{ start }} 章开始；替换原有来源 TXT。</p><div class="form-actions"><button class="button primary" :disabled="action.busy || !state.connected">同步目录并续传</button></div></form>
  <form class="panel form-panel" @submit.prevent="refreshChapters"><h2>刷新已有章节</h2><p class="hint">成功时更新正文，失败或停止时保留旧正文。</p><div class="form-row"><label>刷新范围<select v-model="refreshMode" aria-label="刷新范围"><option value="selected">已勾选的 {{ selected.length }} 章</option><option value="range">输入目录范围</option><option value="all">全部章节</option></select></label><label v-if="refreshMode === 'range'">目录位置<input v-model="range" required placeholder="1-10,15,20-25"></label></div><p class="hint">本次并发 {{ concurrency }}，批间等待 10 秒。</p><div class="form-actions"><button class="button" :disabled="action.busy || !state.connected || (refreshMode === 'selected' && !selected.length)">创建刷新任务</button></div></form></div>
  <section ref="directory" class="panel directory-panel" :aria-busy="busy"><div class="panel-heading"><div><h2>章节目录 <span class="count">{{ source?.total ?? 0 }}</span></h2><p>已选 {{ selected.length }} 章（本页 {{ selectedOnPage }} 章，翻页保留选择） <button v-if="selected.length" class="text-button" @click="selected=[]">清空选择</button></p></div><select v-model="filter" aria-label="按章节状态筛选"><option value="">全部状态</option><option value="completed">已保存</option><option value="pending">待下载</option><option value="failed">失败</option></select></div>
  <Pagination v-model:page="page" v-model:size="size" :total="total" :busy="busy" label="目录顶部翻页" @update:page="scrollToDirectory" />
  <p v-if="error" class="message error" role="alert">{{ error }} <button type="button" class="text-button" @click="load()">重试加载</button></p>
  <div v-if="busy && !data" class="empty" role="status">正在加载章节目录…</div>
  <div v-if="data" class="table-wrap"><table><thead><tr><th class="check-cell"><input type="checkbox" aria-label="选择本页章节" :checked="!!data.items.length && selectedOnPage===data.items.length" :indeterminate="selectedOnPage>0 && selectedOnPage<data.items.length" @change="selectAll"></th><th>目录位置 / 标题</th><th>状态</th><th>最近错误</th><th class="right">正文</th></tr></thead><tbody><tr v-for="chapter in data.items" :key="chapter.id"><td><input v-model="selected" type="checkbox" :value="chapter.id" :aria-label="`选择第 ${chapter.position} 章`"></td><td><span class="position">{{ chapter.position }}</span><strong>{{ titleOf(chapter) }}</strong></td><td><span :class="['badge', chapter.status]">{{ labels[chapter.status] }}</span></td><td class="error-cell" :title="chapter.last_error">{{ chapter.last_error || '—' }}</td><td class="right"><button class="text-button" :disabled="previewBusy" @click="read(chapter.id)">预览 →</button></td></tr></tbody></table></div>
  <div v-if="!busy && data && !data.items.length" class="empty">此筛选下暂无章节。<button v-if="filter" type="button" class="text-button" @click="filter=''">查看全部状态</button></div>
  <Pagination v-model:page="page" v-model:size="size" :total="total" :busy="busy" label="目录底部翻页" @update:page="scrollToDirectory" />
  </section>
  <p v-if="previewBusy && !preview" class="hint" role="status">正在读取正文…</p>
  <Preview v-if="preview" :chapter="preview" :busy="previewBusy" :error="previewError" @close="closePreview" @navigate="read" @retry="previewTarget && read(previewTarget)" />
</template>
