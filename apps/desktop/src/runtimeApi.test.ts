import { describe, expect, it } from 'vitest';
import { resolveApiBase } from './runtimeApi';

describe('installed local service endpoint', () => {
  it('uses only a numeric loopback port passed by the desktop launcher', () => {
    expect(resolveApiBase('?cfPort=18876')).toBe('http://127.0.0.1:18876');
    for (const input of ['?cfPort=https://evil.example', '?cfPort=0', '?cfPort=65536', '?cfPort=1e4']) {
      expect(resolveApiBase(input)).toBe('http://127.0.0.1:8766');
    }
  });
  it('keeps the existing development endpoint when no native port is given', () => {
    expect(resolveApiBase('', 'http://127.0.0.1:18900')).toBe('http://127.0.0.1:18900');
  });
});
