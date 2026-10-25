import { computed, onUnmounted, ref, shallowRef, watch } from 'vue'
import { api, state } from './api'
import type { Page } from './types'

/** 翻页隐藏旧行，轮询保留当前行；同一页请求合并，越界后自动回到最后一页。 */
export function usePagedList<T extends Page<unknown>>(resource: () => string | null) {
  const page=ref(1), size=ref(50), snapshot=shallowRef<T|null>(null)
  const error=ref(''), busy=ref(false), loadedUrl=ref('')
  let current: AbortController | undefined
  let pending: Promise<void> | null=null, pendingUrl='', refreshAgain=false, disposed=false
  watch(resource, () => { page.value=1; snapshot.value=null; loadedUrl.value=''; error.value='' }, {flush:'sync'})
  watch(size, (value,old) => { page.value=Math.floor((page.value-1)*old/value)+1 }, {flush:'sync'})
  const url=computed(() => {
    const path=resource()
    if (!path) return ''
    const [base,search]=path.split('?'), query=new URLSearchParams(search)
    query.set('page',String(page.value)); query.set('page_size',String(size.value))
    return `${base}?${query}`
  })
  const data=computed<T|null>(() => loadedUrl.value===url.value ? snapshot.value : null)
  const total=computed(() => snapshot.value?.total || 0)
  async function load(background=false): Promise<void> {
    const target=url.value
    if (!target || disposed) { current?.abort(); return }
    if (pending && pendingUrl===target) { if (background) refreshAgain=true; return pending }
    current?.abort()
    const controller=new AbortController(); current=controller; pendingUrl=target; refreshAgain=false
    busy.value=true; error.value=''
    const request=api<T>(target,{signal:controller.signal}).then(result => {
      if (disposed || controller.signal.aborted || current!==controller || url.value!==target) return
      snapshot.value=result; loadedUrl.value=target
      const last=Math.max(1,Math.ceil(result.total/size.value))
      if (page.value>last) page.value=last
    }).catch((e:Error) => {
      if (!disposed && current===controller && !controller.signal.aborted) error.value=e.message
    }).finally(() => {
      if (current!==controller) return
      pending=null; busy.value=false
      if (refreshAgain && !disposed && url.value===target) { refreshAgain=false; void load() }
    })
    pending=request
    return request
  }
  watch(url, () => { void load() }, {immediate:true})
  watch(() => state.revision, () => { void load(true) })
  onUnmounted(() => { disposed=true; current?.abort() })
  return {page,size,data,total,error,busy,load}
}
