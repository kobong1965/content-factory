import type { FinishedLibraryData, FootageBatch } from './footageBatches';
export type FinishedItem = FinishedLibraryData['groups'][number]['items'][number];
export function finishedFolders(data: FinishedLibraryData, batches: FootageBatch[]) {
  const folders = new Map<string, { id: string; title: string; items: FinishedItem[] }>();
  for (const batch of batches) folders.set(batch.id, { id: batch.id, title: batch.title, items: batch.candidates.map(candidate => ({ batch_id: batch.id, batch_title: batch.title, revision: batch.revision, candidate })) });
  for (const group of data.groups) for (const item of group.items) {
    let folder = folders.get(item.batch_id);
    if (!folder) { folder = {id:item.batch_id, title:item.batch_title, items:[]}; folders.set(item.batch_id, folder); }
    if (!folder.items.some(i => i.candidate.id === item.candidate.id)) folder.items.push(item);
  }
  return [...folders.values()];
}
