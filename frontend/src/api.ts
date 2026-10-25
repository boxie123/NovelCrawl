import { computed, reactive } from 'vue'
import type { AppInfo, Edition, Source, Task } from './types'
import { terminal } from './types'
import { validateTask } from './validation'

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try { response=await fetch(`/api${path}`, { ...options, headers: { 'Content-Type':'application/json', ...options.headers } }) }
  catch (error) { if ((error as Error).name==='AbortError') throw error; throw new Error('无法连接本地服务，请检查启动终端后重试') }
  let data
  try { data=await response.json() } catch { throw new Error(`服务返回了无法读取的响应（${response.status}），请重试`) }
  if (!response.ok) {
    const fields = data.error?.fields?.map((e: {message: string}) => e.message).join('；')
    throw new Error(fields || data.error?.message || data.detail || '操作失败')
  }
  return data as T
}
export const state = reactive({ sources: [] as Source[], editions: [] as Edition[], tasks: [] as Task[], info: null as AppInfo | null, connected: true, loaded: false, revision: 0 })
export const activeTasks = computed(() => state.tasks.filter(t => !terminal(t)))
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let enabled = false
let loading: Promise<void> | null = null
export function refresh(): Promise<void> {
  if (loading) return loading
  controller = new AbortController()
  const signal = controller.signal
  loading = Promise.all([api<Source[]>('/sources', { signal }), api<Edition[]>('/editions', { signal }), api<Task[]>('/tasks', { signal }), api<AppInfo>('/app', { signal })])
    .then(([sources, editions, tasks, info]) => {
      if (signal.aborted) return
      const changed = !state.connected || JSON.stringify([sources, editions, tasks]) !== JSON.stringify([state.sources, state.editions, state.tasks])
      Object.assign(state, { sources, editions, tasks, info, connected: true, loaded: true })
      if (changed) state.revision++
    })
    .catch((error: Error) => { if (error.name !== 'AbortError') state.connected = false })
    .finally(() => { loading = null })
  return loading
}
export function startPolling() {
  if (enabled) return
  enabled = true
  const tick = async () => {
    await refresh()
    if (enabled) timer = setTimeout(tick, activeTasks.value.length ? 1000 : 5000)
  }
  void tick()
}
export function stopPolling() { enabled = false; clearTimeout(timer); controller?.abort() }
export async function submitTask(body: Record<string, unknown>): Promise<Task> {
  validateTask(body)
  const task = await api<Task>('/tasks', { method:'POST', body:JSON.stringify({ ...body, request_id: crypto.randomUUID() }) })
  await refresh()
  return task
}
export function useAction() {
  const action = reactive({ busy:false, error:'', success:'' })
  async function run(body: Record<string, unknown>) {
    if (action.busy) return
    action.busy = true; action.error = ''; action.success = ''
    try { await submitTask(body); action.success = '任务已加入队列，可在任务中心查看进度。' }
    catch (error) { action.error = (error as Error).message }
    finally { action.busy = false }
  }
  return { action, run }
}
