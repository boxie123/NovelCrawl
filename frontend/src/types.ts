export type Progress = { total: number; completed: number; pending: number; failed: number }
export type Source = Progress & { id: number; title: string; site_name: string; catalogue_url: string; output_name: string; progress?: Progress }
export type Chapter = { id: number; source_id: number; position: number; catalogue_title: string; normalized_title: string; status: string; last_error?: string; content?: string; previous_id?: number | null; next_id?: number | null }
export type Edition = { id: number; name: string; description?: string; output_name: string }
export type MergedChapter = { id: number; position: number; title: string; parts: Chapter[] }
export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type Task = { id: string; kind: string; status: string; phase: string; source_id?: number; parameters: Record<string, unknown>; total: number; processed: number; succeeded: number; failed: number; attempt?: number; current_chapters?: number[]; wait_until?: number; source_progress?: Progress; error?: string; result?: { download_url?: string; path?: string; edition_id?: number }; created_at: number }
export type AppInfo = { name: string; version: string; sites: { key: string; domains: string[] }[]; stopping: boolean; cleanup_failed: boolean; database_path: string }
export const labels: Record<string, string> = {
  queued:'排队中', running:'进行中', stopping:'正在停止', succeeded:'已完成', completed_with_errors:'完成 · 有失败', failed:'失败', cancelled:'已停止',
  pending:'待下载', completed:'已保存', starting:'启动浏览器', catalogue:'同步目录', fetching:'下载章节', warmup:'预访问目录', waiting:'批间等待', exporting:'导出 TXT', merging:'更新合并版本',
  download:'添加并下载', resume:'续传', refresh:'刷新章节', export_source:'导出来源', export_merged:'导出合并版', merge_create:'创建合并版', merge_append:'追加章节', merge_compose:'组合章节',
}
export const terminal = (t: Task) => ['succeeded','completed_with_errors','failed','cancelled'].includes(t.status)
export const titleOf = (c: Chapter) => c.normalized_title || c.catalogue_title || `目录第 ${c.position} 章`
