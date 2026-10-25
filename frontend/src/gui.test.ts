import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import Pagination from './components/Pagination.vue'
import Sources from './pages/Sources.vue'
import { api, refresh, startPolling, state, stopPolling } from './api'

const response = (data: unknown, ok=true) => Promise.resolve({ ok, json: async () => data } as Response)
beforeEach(() => { Object.assign(state, { sources:[], editions:[], tasks:[], loaded:true, connected:true, revision:0 }); vi.restoreAllMocks() })
afterEach(() => { stopPolling(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('分页', () => {
  it('禁用边界并发出明确页码', async () => {
    const wrapper=mount(Pagination, { props:{ page:1,total:101 } })
    const previous=wrapper.findAll('button').find(b=>b.text()==='上一页')!
    const next=wrapper.findAll('button').find(b=>b.text()==='下一页')!
    expect(previous.attributes('disabled')).toBeDefined()
    await next.trigger('click')
    expect(wrapper.emitted('update:page')).toEqual([[2]])
    await wrapper.find('input').setValue('9')
    await wrapper.find('input').trigger('keydown.enter')
    expect(wrapper.emitted('update:page')).toHaveLength(1)
    expect(wrapper.get('[role=alert]').text()).toContain('1～3')
    wrapper.unmount()
  })
})

describe('API 与状态', () => {
  it('展示后端字段校验错误', async () => {
    vi.stubGlobal('fetch', vi.fn(() => response({ error:{ fields:[{message:'并发数必须在 1～5'}] } }, false)))
    await expect(api('/tasks')).rejects.toThrow('并发数必须在 1～5')
  })
  it('网络中断不覆盖服务端任务为失败', async () => {
    state.tasks=[{id:'a',kind:'resume',status:'running',phase:'fetching',parameters:{},total:3,processed:1,succeeded:1,failed:0,created_at:0}]
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))))
    await refresh()
    expect(state.connected).toBe(false)
    expect(state.tasks[0].status).toBe('running')
  })
  it('轮询不重叠且停止后不再调度', async () => {
    vi.useFakeTimers()
    let resolve!: (value: Response) => void
    const pending=new Promise<Response>(r => {resolve=r})
    const fetch=vi.fn(() => pending)
    vi.stubGlobal('fetch',fetch)
    startPolling()
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetch).toHaveBeenCalledTimes(4)
    stopPolling()
    resolve({ok:true,json:async () => []} as unknown as Response)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetch).toHaveBeenCalledTimes(4)
  })
})

describe('来源书库', () => {
  it('表单提交包含起始位置和本次并发，按钮防止重复提交', async () => {
    const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Sources},{path:'/tasks',component:{template:'<div />'}}]})
    await router.push('/'); await router.isReady()
    const calls: Record<string,unknown>[]=[]
    vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit) => {
      if (init?.method==='POST') { calls.push(JSON.parse(init.body as string)); return response({id:'task'}) }
      return response(url.endsWith('/app') ? {sites:[]} : [])
    }))
    const wrapper=mount(Sources,{global:{plugins:[router]}})
    await wrapper.get('.page-heading button').trigger('click')
    await wrapper.get('input[type=url]').setValue('https://www.sudugu.org/book/')
    const numbers=wrapper.findAll('input[type=number]')
    await numbers[0].setValue('20'); await numbers[1].setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({kind:'download',start_position:20,concurrency:2})
    expect(wrapper.text()).toContain('任务已加入队列')
    wrapper.unmount()
  })
})

import SourceDetail from './pages/SourceDetail.vue'
import { validateTask } from './validation'

describe('刷新选择与校验', () => {
  it('无效范围和并发在发请求前拒绝', () => {
    expect(() => validateTask({kind:'refresh',concurrency:6,all:true})).toThrow('1～5')
    expect(() => validateTask({kind:'merge_append',chapters:'1,3'})).toThrow('连续')
    expect(() => validateTask({kind:'refresh',concurrency:3,chapters:'9-1'})).toThrow('无效')
  })
  it('刷新提交稳定章节 ID，过滤后清空旧选择', async () => {
    const router=createRouter({history:createMemoryHistory(),routes:[{path:'/sources/:id',component:SourceDetail},{path:'/',component:{template:'<div />'}}]})
    await router.push('/sources/7'); await router.isReady()
    const calls: Record<string,unknown>[]=[]
    vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit) => {
      if (init?.method==='POST') { calls.push(JSON.parse(init.body as string)); return response({id:'task'}) }
      if (url.includes('/chapters?')) return response({items:[{id:42,position:2,source_id:7,status:'completed',normalized_title:'章节'}],total:1,page:1,page_size:50})
      return response(url.endsWith('/app') ? {sites:[]} : [])
    }))
    const wrapper=mount(SourceDetail,{global:{plugins:[router]}})
    await flushPromises()
    await wrapper.get('input[aria-label="选择第 2 章"]').setValue(true)
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
    expect(calls[0]).toMatchObject({kind:'refresh',source_id:7,chapter_ids:[42],concurrency:3})
    await wrapper.get('select[aria-label="按章节状态筛选"]').setValue('pending')
    await flushPromises()
    expect(wrapper.text()).toContain('已选 0 章')
    wrapper.unmount()
  })
})
