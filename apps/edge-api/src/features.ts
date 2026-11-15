/** Supabase server-only facade over HTTPS. No postgres driver, Hyperdrive, or D1 fallback. */
export interface FeaturesEnv { FEATURES_URL?: string; FEATURES_KEY?: string; FEATURES_SERVICE?: Fetcher }
export class FeatureError extends Error { constructor(public status: number) { super('Feature service unavailable'); } }
export async function featureCall<T = Record<string, unknown>>(env: FeaturesEnv, op: string, payload: unknown = {}): Promise<T> {
  if (!env.FEATURES_URL || !env.FEATURES_KEY) throw new FeatureError(503);
  const url = new URL('/rest/v1/rpc/rpc', env.FEATURES_URL);
  if (url.protocol !== 'https:') throw new FeatureError(503);
  const request = new Request(url, { method:'POST', headers: {
    'Content-Type':'application/json', 'Content-Profile':'stockfinder_features', 'Accept-Profile':'stockfinder_features',
    apikey:env.FEATURES_KEY, Authorization:`Bearer ${env.FEATURES_KEY}`
  }, body:JSON.stringify({op,payload}), signal:AbortSignal.timeout(10000) });
  const response = await (env.FEATURES_SERVICE ? env.FEATURES_SERVICE.fetch(request) : fetch(request));
  if (!response.ok) {
    const error = await response.json().catch(()=>({})) as { code?:string };
    throw new FeatureError(error.code === 'P0002' ? 404 : ['22023','22P02'].includes(error.code ?? '') ? 400 : 503);
  }
  return response.json() as Promise<T>;
}
