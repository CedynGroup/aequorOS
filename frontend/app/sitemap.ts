import type { MetadataRoute } from 'next';

const BASE_URL = 'https://aequoros.com';

export default function sitemap(): MetadataRoute.Sitemap {
  const routes = ['', '/product', '/company', '/investors', '/contact'];
  return routes.map((path) => ({
    url: `${BASE_URL}${path}`,
    changeFrequency: 'monthly',
    priority: path === '' ? 1 : 0.8,
  }));
}
