import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import Pagination from './components/Pagination.vue'
import { usePagedList } from './pagination'
import { state } from './api'
import type { Chapter, Page } from './types'

const reply=(data:unknown) => ({ok:true,json:async()=>data}) as Response
function fixture(resource=ref('/sources/1/chapters')) {
  let list!: ReturnType<typeof usePagedList<Page<Chapter>>>
  const wrapper=mount(defineComponent({setup(){list=usePagedList<Page<Chapter>>(()=>resource.value);return list},template:'<div>{{ data?.items[0]?.normalized_title }}</div>'}))
  return {wrapper,list,resource}
}
const row=(id:number) => ({id,source_id:1,position:id,normalized_title:`章节${id}`,catalogue_title:'',status:'pending'})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})

describe('分页控件',()=>{
  it('跳转需确认，非法页码提示后可纠正；空列表禁用跳转',async()=>{
    const wrapper=mount(Pagination,{props:{page:2,total:260,size:50}})
    const input=wrapper.get('input')
    await input.setValue('4')
    expect(wrapper.emitted('update:page')).toBeUndefined()
    await input.trigger('keydown.enter')
    expect(wrapper.emitted('update:page')).toEqual([[4]])
    await input.setValue('1.5');await input.trigger('keydown.enter')
    expect(wrapper.get('[role=alert]').text()).toContain('整数')
    await input.setValue('2');await input.trigger('keydown.enter')
    expect(wrapper.find('[role=alert]').exists()).toBe(false)
    await wrapper.setProps({page:4})
    expect(wrapper.find('[role=alert]').exists()).toBe(false)
    await wrapper.get('select').setValue('100')
    expect(wrapper.emitted('update:size')).toEqual([[100]])
    await wrapper.setProps({total:0,page:1})
    expect(wrapper.findAll('button').every(b=>b.attributes('disabled')!==undefined)).toBe(true)
    wrapper.unmount()
  })
  it('长目录页码数量有界，并标出当前页',()=>{
    const wrapper=mount(Pagination,{props:{page:5000,total:500000,size:50}})
    expect(wrapper.findAll('.page-numbers button')).toHaveLength(7)
    expect(wrapper.get('[aria-current=page]').text()).toBe('5000')
    wrapper.unmount()
  })
})

describe('目录分页请求',()=>{
  it('目录增长保留当前页，缩减后回退最后有效页',async()=>{
    let total=160
    vi.stubGlobal('fetch',vi.fn((url:string)=>{
      const p=Number(new URL(url,'http://local').searchParams.get('page'))
      return Promise.resolve(reply({items:p<=Math.ceil(total/50)?[row((p-1)*50+1)]:[],total,page:p,page_size:50}))
    }))
    const {wrapper,list}=fixture();await flushPromises()
    list.page.value=3;await flushPromises()
    total=300;await list.load(true)
    expect(list.page.value).toBe(3)
    total=51;await list.load(true);await flushPromises()
    expect(list.page.value).toBe(2)
    expect(list.data.value?.items[0].id).toBe(51)
    wrapper.unmount()
  })
  it('调整每页数量保留原位置，筛选改变重置第一页',async()=>{
    const fetch=vi.fn((url:string)=>Promise.resolve(reply({items:[row(1)],total:301,page:Number(new URL(url,'http://local').searchParams.get('page')),page_size:50})))
    vi.stubGlobal('fetch',fetch)
    const {wrapper,list,resource}=fixture();await flushPromises()
    list.page.value=3;await flushPromises()
    list.size.value=100;await flushPromises()
    expect(list.page.value).toBe(2)
    expect(fetch.mock.calls.at(-1)![0]).toContain('page_size=100')
    resource.value='/sources/1/chapters?status=failed';await flushPromises()
    expect(list.page.value).toBe(1)
    wrapper.unmount()
  })
  it('切换来源立即清掉旧行，迟到响应不能覆盖新来源',async()=>{
    const callbacks=new Map<string,(r:Response)=>void>()
    vi.stubGlobal('fetch',vi.fn((url:string)=>new Promise<Response>(resolve=>callbacks.set(url,resolve))))
    const {wrapper,list,resource}=fixture()
    resource.value='/sources/2/chapters';await flushPromises()
    expect(list.data.value).toBeNull()
    callbacks.get('/api/sources/2/chapters?page=1&page_size=50')!(reply({items:[row(22)],total:1,page:1,page_size:50}));await flushPromises()
    callbacks.get('/api/sources/1/chapters?page=1&page_size=50')!(reply({items:[row(11)],total:1,page:1,page_size:50}));await flushPromises()
    expect(list.data.value?.items[0].id).toBe(22)
    resource.value='/sources/3/chapters';await flushPromises()
    expect(list.data.value).toBeNull()
    wrapper.unmount()
  })
  it('后台更新不会不断取消慢查询，完成后补一次最新读取',async()=>{
    let resolve!: (r:Response)=>void
    const fetch=vi.fn(()=>new Promise<Response>(r=>{resolve=r}))
    vi.stubGlobal('fetch',fetch)
    const {wrapper,list}=fixture();await flushPromises()
    state.revision++;await flushPromises();state.revision++;await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(1)
    resolve(reply({items:[row(1)],total:1,page:1,page_size:50}));await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(2)
    resolve(reply({items:[row(2)],total:1,page:1,page_size:50}));await flushPromises()
    expect(list.data.value?.items[0].id).toBe(2)
    expect(list.busy.value).toBe(false)
    wrapper.unmount()
  })
  it('翻页失败不显示错页旧数据，重试恢复',async()=>{
    let failed=false
    vi.stubGlobal('fetch',vi.fn(()=>failed?Promise.reject(new Error('offline')):Promise.resolve(reply({items:[row(1)],total:100,page:1,page_size:50}))))
    const {wrapper,list}=fixture();await flushPromises()
    failed=true;list.page.value=2;await flushPromises()
    expect(list.data.value).toBeNull()
    expect(list.error.value).toContain('无法连接')
    failed=false;await list.load();expect(list.error.value).toBe('');expect(list.data.value).not.toBeNull()
    wrapper.unmount()
  })
})
