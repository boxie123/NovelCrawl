/** 提交前的业务校验；后端仍独立校验全部参数。 */
export function validateTask(body: Record<string, unknown>) {
  const positive = (value: unknown) => Number.isSafeInteger(value) && Number(value) > 0
  const kind=body.kind
  if (['download','resume','refresh'].includes(String(kind))) {
    if (!positive(body.concurrency) || Number(body.concurrency)>5) throw new Error('本次并发数必须是 1～5 的整数')
    if (body.start_position != null && !positive(body.start_position)) throw new Error('起始位置必须是正整数')
  }
  if (kind==='download') {
    let url: URL
    try { url=new URL(String(body.url)) } catch { throw new Error('请输入完整的目录 URL') }
    if (!['http:','https:'].includes(url.protocol)) throw new Error('目录 URL 必须使用 HTTP 或 HTTPS')
  }
  if (kind==='merge_create' && !String(body.name || '').trim()) throw new Error('版本名称不能为空')
  if (body.chapter_ids != null && (!Array.isArray(body.chapter_ids) || !body.chapter_ids.length || body.chapter_ids.some(id => !positive(id)))) throw new Error('请至少选择一个章节')
  if (body.chapters != null) {
    const ranges=String(body.chapters).split(',').map(part => part.trim())
    const positions=new Set<number>()
    for (const part of ranges) {
      if (!/^\d+(?:-\d+)?$/.test(part)) throw new Error('章节范围格式应为 1-10,15')
      const [start,end=start]=part.split('-').map(Number)
      if (!positive(start) || !positive(end) || end<start || end-start>99999) throw new Error('章节范围无效或过大')
      for (let i=start;i<=end;i++) {
        positions.add(i)
        if (positions.size>100000) throw new Error('每次最多选择 100000 章')
      }
    }
    if (kind==='merge_append') {
      const sorted=[...positions].sort((a,b)=>a-b)
      if (sorted.at(-1)!-sorted[0]+1!==sorted.length) throw new Error('追加范围必须连续')
    }
  }
}
