export default async function handler(req, res) {
  const { code, state } = req.query;
  const backendUrl = `http://178.105.30.215:8000/api/youtube/callback?code=${code}&state=${state}`;

  console.log(`[Proxy] GET ${backendUrl}`);

  try {
    const response = await fetch(backendUrl, {
      method: 'GET',
    });

    const data = await response.text();
    res.status(response.status).setHeader('Content-Type', 'text/html');
    res.send(data);
  } catch (error) {
    console.error('[Proxy Error]', error);
    res.status(500).send(`<html><body>Error: ${error.message}</body></html>`);
  }
}
