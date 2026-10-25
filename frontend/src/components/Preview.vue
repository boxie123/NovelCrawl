<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { Chapter } from '../types'
import { labels, titleOf } from '../types'
const props=defineProps<{ chapter: Chapter; busy?: boolean; error?: string }>()
const emit = defineEmits<{ close: []; navigate:[id:number]; retry:[] }>()
const dialog = ref<HTMLDialogElement>(), content=ref<HTMLElement>()
let previous: HTMLElement | null = null
onMounted(async () => { previous = document.activeElement as HTMLElement; await nextTick(); dialog.value?.showModal() })
onUnmounted(() => previous?.focus())
watch(() => props.chapter.id, async () => { await nextTick(); if(content.value) content.value.scrollTop=0 })
</script>
<template>
  <dialog ref="dialog" class="preview" aria-label="章节正文预览" :aria-busy="busy" @cancel.prevent="emit('close')">
    <header><div><span class="eyebrow">正文预览 · 目录位置 {{ chapter.position }}</span><h2>{{ titleOf(chapter) }}</h2></div><button class="icon-button" aria-label="关闭正文预览" @click="emit('close')">×</button></header>
    <div ref="content" class="preview-content"><p v-if="error" class="message error" role="alert">切换失败，仍显示当前章节：{{ error }} <button class="text-button" @click="emit('retry')">重试</button></p><p v-if="chapter.last_error" class="message error">最近错误：{{ chapter.last_error }}</p><pre v-if="chapter.content">{{ chapter.content }}</pre><div v-else class="empty"><p>{{ labels[chapter.status] }}</p><span>此章节尚无已保存的正文。</span></div></div>
    <footer class="preview-navigation"><button class="button" :disabled="busy || !chapter.previous_id" @click="chapter.previous_id && emit('navigate',chapter.previous_id)">← 上一章</button><span role="status">{{ busy ? '正在读取正文…' : '按来源目录顺序预览' }}</span><button class="button" :disabled="busy || !chapter.next_id" @click="chapter.next_id && emit('navigate',chapter.next_id)">下一章 →</button></footer>
  </dialog>
</template>
