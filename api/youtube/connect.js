export default async function handler(req, res) {
  const backendUrl = 'http://178.105.30.215:8000/api/youtube/connect';

  console.log(`[Proxy] GET ${backendUrl}`);

  try {
    const response = await fetch(backendUrl, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
    });

    const data = await response.json();
    res.status(response.status).json(data);
  } catch (error) {
    console.error('[Proxy Error]', error);
    res.status(500).json({
      error: error.message,
      backend: backendUrl,
    });
  }
}
