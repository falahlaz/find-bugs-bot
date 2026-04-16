# Cloudflare Worker — OpenAI Reverse Proxy

## 1. Create Worker

```bash
npm create cloudflare@latest
# Choose: "Hello World" template, "TypeScript", no framework
# Name: openai-proxy (or any name you want)
cd openai-proxy
```

## 2. Replace `src/index.ts` with this:

```ts
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Only proxy chat completions and models endpoints
    const proxyPaths = ['/v1/chat/completions', '/v1/models'];
    const shouldProxy = proxyPaths.some(p => url.pathname.startsWith(p));

    if (!shouldProxy) {
      return new Response('Not found', { status: 404 });
    }

    const upstream = 'https://api.openai.com';
    const upstreamUrl = upstream + url.pathname + url.search;

    const headers: Record<string, string> = {};
    request.headers.forEach((value, key) => {
      if (key.toLowerCase() !== 'host') {
        headers[key] = value;
      }
    });

    const upstreamReq = new Request(upstreamUrl, {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.text(),
    });

    const upstreamRes = await fetch(upstreamReq);

    const responseHeaders = new Headers();
    upstreamRes.headers.forEach((value, key) => {
      if (!['content-encoding', 'transfer-encoding', 'connection'].includes(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    });

    return new Response(await upstreamRes.text(), {
      status: upstreamRes.status,
      headers: responseHeaders,
    });
  },
};
```

## 3. Add wrangler types

```bash
npm install -D @cloudflare/workers-types
```

## 4. Configure `wrangler.toml`

```toml
name = "openai-proxy"
main = "src/index.ts"
compatibility_date = "2024-01-01"
```

## 5. Add CORS headers (optional, for browser access)

```ts
// Before the return statement in the proxy block, add:
responseHeaders.set('Access-Control-Allow-Origin', '*');
responseHeaders.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
responseHeaders.set('Access-Control-Allow-Headers', 'Content-Type, Authorization');
```

## 6. Deploy

```bash
npx wrangler deploy
# Output: https://openai-proxy.<your-subdomain>.workers.dev
```

## 7. Set in `.env`

```bash
LLM_BASE_URL=https://openai-proxy.<your-subdomain>.workers.dev/v1
LLM_PROXY=
LLM_SKIP_SSL_VERIFY=false  # Cloudflare uses valid certs
```

## 8. Test

```bash
curl https://openai-proxy.<your-subdomain>.workers.dev/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

## Cost

Cloudflare Workers free tier: **100,000 req/day**, 10ms CPU time per req. OpenAI proxy uses ~50ms CPU. Well within free tier for personal use.

## Troubleshooting

| Issue | Fix |
|---|---|
| 403 on Worker | Check Workers Paid plan required for outbound fetch to external domains (free tier allows this) |
| CORS error in bot | Bot uses server-side HTTP, CORS not an issue |
| `LLM_SKIP_SSL_VERIFY=true` needed | Set in `.env` if corporate proxy intercepts Cloudflare cert |