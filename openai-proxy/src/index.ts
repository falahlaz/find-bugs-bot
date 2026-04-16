export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

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

    responseHeaders.set('Access-Control-Allow-Origin', '*');
    responseHeaders.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    responseHeaders.set('Access-Control-Allow-Headers', 'Content-Type, Authorization');

    return new Response(await upstreamRes.text(), {
      status: upstreamRes.status,
      headers: responseHeaders,
    });
  },
};