export default async function handler(req, res) {
  const backendUrl = 'http://178.105.30.215:8000/api/channels';

  try {
    let response;
    if (req.method === 'POST') {
      response = await fetch(backendUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(req.body),
      });
    } else {
      response = await fetch(backendUrl, {
        method: 'GET',
      });
    }

    const data = await response.json();
    res.status(response.status).json(data);
  } catch (error) {
    console.error('[Proxy Error]', error);
    res.status(500).json({ error: error.message });
  }
}
