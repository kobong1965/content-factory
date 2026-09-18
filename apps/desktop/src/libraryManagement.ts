import { runtimeApiBaseUrl } from './runtimeApi';
export const libraryApi = runtimeApiBaseUrl;
export type LibraryEntry = { title?: string; group?: string; deleted?: boolean };
export type LibraryOrganization = { revision: number; entries: Record<string, LibraryEntry> };
export async function libraryOrganization(signal?: AbortSignal): Promise<LibraryOrganization> {
  return read(await fetch(`${libraryApi}/s7/library-management`, { signal }));
}
async function read(response: Response): Promise<LibraryOrganization> {
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '整理失败，请检查名称并重试');
  return data as LibraryOrganization;
}
export async function organizeLibrary(revision: number, keys: string[], changes: LibraryEntry): Promise<LibraryOrganization> {
  return read(await fetch(`${libraryApi}/s7/library-management`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, keys, ...changes }),
  }));
}
export const archiveUrl = (id: string) => `${libraryApi}/s7/library-management/${encodeURIComponent(id)}/archive?download=true`;
