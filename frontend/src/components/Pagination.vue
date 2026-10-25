<script setup lang="ts">
import { computed, ref, watch } from 'vue'
const props = withDefaults(defineProps<{ page: number; total: number; size?: number; busy?: boolean; label?: string }>(), { size:50, busy:false, label:'章节分页' })
const emit = defineEmits<{ 'update:page': [value: number]; 'update:size': [value:number] }>()
const pages = computed(() => Math.max(1, Math.ceil(props.total/props.size)))
const draft = ref(String(props.page)), error = ref('')
watch(() => props.page, value => { draft.value=String(value); error.value='' })
watch(pages, () => { draft.value=String(props.page); error.value='' })
const numbers = computed(() => {
  const selected=new Set([1, pages.value])
  for (let n=Math.max(1,props.page-2);n<=Math.min(pages.value,props.page+2);n++) selected.add(n)
  const result: (number|string)[]=[]
  let previous=0
  for (const n of [...selected].sort((a,b)=>a-b)) {
    if (previous && n-previous>1) result.push(`gap-${n}`)
    result.push(n); previous=n
  }
  return result
})
function go(value:number) {
  if (props.busy || value<1 || value>pages.value) return
  error.value=''; draft.value=String(value)
  if (value!==props.page) emit('update:page',value)
}
function jump() {
  const value=Number(draft.value)
  if (!/^\d+$/.test(draft.value.trim()) || !Number.isSafeInteger(value) || value<1 || value>pages.value) {
    error.value=`请输入 1～${pages.value} 的整数页码`; return
  }
  go(value)
}
</script>
<template>
  <div class="pagination" role="group" :aria-label="label" :aria-busy="busy">
    <div class="pagination-summary"><span>共 {{ total.toLocaleString() }} 章<span v-if="total"> · 第 {{ Math.min((page-1)*size+1,total).toLocaleString() }}–{{ Math.min(page*size,total).toLocaleString() }} 条</span></span><label>每页 <select aria-label="每页章节数" :value="size" :disabled="busy" @change="emit('update:size',Number(($event.target as HTMLSelectElement).value))"><option v-for="count in [25,50,100,200]" :key="count" :value="count">{{ count }} 章</option></select></label></div>
    <div class="pagination-controls">
      <button type="button" class="button small page-edge" :disabled="busy || page<=1" @click="go(1)">首页</button>
      <button type="button" class="button small" :disabled="busy || page<=1" @click="go(page-1)">上一页</button>
      <div class="page-numbers"><template v-for="number in numbers" :key="number"><button v-if="typeof number==='number'" type="button" class="button small" :aria-label="`第 ${number} 页`" :aria-current="page===number ? 'page' : undefined" :disabled="busy || !total" @click="go(number)">{{ number }}</button><span v-else aria-hidden="true">…</span></template></div>
      <button type="button" class="button small" :disabled="busy || page>=pages" @click="go(page+1)">下一页</button>
      <button type="button" class="button small page-edge" :disabled="busy || page>=pages" @click="go(pages)">末页</button>
      <div class="page-jump"><label>第 <input v-model="draft" aria-label="页码" class="page-input" type="text" inputmode="numeric" :aria-invalid="!!error" :disabled="busy || !total" @keydown.enter.prevent="jump" @keydown.esc="draft=String(page);error=''"> / {{ pages }} 页</label><button type="button" class="button small" :disabled="busy || !total" @click="jump">跳转</button></div>
    </div><p v-if="error" class="pagination-error" role="alert">{{ error }}</p>
  </div>
</template>
