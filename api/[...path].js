export default async function handler(req, res) {
  const path = Array.isArray(req.query.path) ? req.query.path.join('/') : req.query.path || '';
  const queryString = Object.keys(req.query)
    .filter(k => k !== 'path')
    .map(k => `${k}=${encodeURIComponent(req.query[k])}`)
    .join('&');

  const backendUrl = `http://178.105.30.215:8000/api/${path}${queryString ? '?' + queryString : ''}`;

  console.log(`[API Proxy] ${req.method} ${backendUrl}`);

  try {
    const fetchOptions = {
      method: req.method,
      headers: {
        'Content-Type': 'application/json',
      },
    };

    if (req.method !== 'GET' && req.method !== 'HEAD') {
      fetchOptions.body = JSON.stringify(req.body);
    }

    const response = await fetch(backendUrl, fetchOptions);
    const data = await response.text();

    res.status(response.status);

    // Copy relevant headers
    const contentType = response.headers.get('content-type');
    if (contentType) {
      res.setHeader('Content-Type', contentType);
    }

    // Handle SSE (Server-Sent Events)
    if (contentType && contentType.includes('text/event-stream')) {
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');
      res.write(data);
      res.end();
    } else {
      try {
        res.json(JSON.parse(data));
      } catch {
        res.send(data);
      }
    }
  } catch (error) {
    console.error('[API Proxy Error]', error);
    res.status(500).json({
      error: error.message,
      url: backendUrl
    });
  }
}
