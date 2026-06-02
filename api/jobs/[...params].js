export default async function handler(req, res) {
  const params = Array.isArray(req.query.params) ? req.query.params.join('/') : req.query.params || '';
  const backendUrl = `http://178.105.30.215:8000/api/jobs/${params}`;

  console.log(`[Proxy] ${req.method} ${backendUrl}`);

  try {
    const response = await fetch(backendUrl, {
      method: req.method,
      headers: {
        'Content-Type': 'application/json',
      },
      body: req.method !== 'GET' ? JSON.stringify(req.body) : undefined,
    });

    const contentType = response.headers.get('content-type');

    // Handle streaming responses (SSE)
    if (contentType && contentType.includes('text/event-stream')) {
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');

      // Read the stream from backend
      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        res.write(decoder.decode(value));
      }
      res.end();
    } else {
      const data = await response.text();
      try {
        const json = JSON.parse(data);
        res.status(response.status).json(json);
      } catch {
        res.status(response.status).send(data);
      }
    }
  } catch (error) {
    console.error('[Proxy Error]', error);
    res.status(500).json({ error: error.message });
  }
}
