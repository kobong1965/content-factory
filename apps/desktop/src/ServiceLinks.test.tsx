import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ServiceLinks, BILLING_URL } from './ServiceLinks';

describe('service billing entry', () => {
  it('uses the exact approved dashboard without passing credentials', () => {
    expect(BILLING_URL).toBe('https://apikey.fun/dashboard');
    const html = renderToStaticMarkup(<ServiceLinks />);
    expect(html).toContain('查看余额与用量');
    expect(html).toContain('APIKEY.FUN');
    expect(html).not.toContain('api_key=');
  });
});
