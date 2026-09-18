import { describe, expect, it } from 'vitest';
import { finishedFolders } from './finishedFolders';
import type { FinishedLibraryData, FootageBatch, FootageCandidate } from './footageBatches';
describe('finished folders', () => {
  it('keeps same-title batches separate and retains pending files without duplicate approved records', () => {
    const c = {id:'01',review_status:'pending'} as FootageCandidate;
    const batches = ['a','b'].map(id => ({id,title:'同名批次',revision:1,candidates:[c]}) as FootageBatch);
    const data = {total:1,unavailable:[],groups:[{sku:'J82',count:1,items:[{batch_id:'a',batch_title:'同名批次',revision:1,candidate:c}]}]} as FinishedLibraryData;
    const result = finishedFolders(data,batches);
    expect(result.map(f=>f.id)).toEqual(['a','b']);
    expect(result.map(f=>f.items.length)).toEqual([1,1]);
    expect(result[0]?.items[0]?.candidate.review_status).toBe('pending');
  });
  it('retains script outputs and their download addresses', () => {
    const item = {batch_id:'script-a',batch_title:'历史脚本',candidate:{id:'v1'},video_url:'/s7/outputs/o/resources/video'} as FinishedLibraryData['groups'][number]['items'][number];
    expect(finishedFolders({total:1,unavailable:[],groups:[{sku:'J85',count:1,items:[item]}]},[])[0]?.items[0]).toEqual(item);
  });
});
