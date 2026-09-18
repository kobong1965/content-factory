/** The native launcher supplies a loopback port, never a remote API origin. */
export function resolveApiBase(search: string, configured?: string): string {
  const port = new URLSearchParams(search).get('cfPort');
  if (port && /^\d{4,5}$/.test(port) && Number(port) >= 1024 && Number(port) <= 65535) {
    return `http://127.0.0.1:${Number(port)}`;
  }
  return configured ?? 'http://127.0.0.1:8766';
}

export const runtimeApiBaseUrl = resolveApiBase(
  typeof window === 'undefined' ? '' : window.location.search,
  import.meta.env.VITE_API_URL,
);
