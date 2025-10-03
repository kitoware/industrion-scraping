import { NextResponse } from 'next/server';
import { spawn } from 'node:child_process';

export const runtime = 'nodejs';
export const maxDuration = 60;

const isVercel = process.env.VERCEL === '1';
const forceRemotePipeline = process.env.FORCE_REMOTE_PIPELINE === '1';
const pipelineEndpointEnv = process.env.PIPELINE_ENDPOINT?.trim();
const allowLocalFallbackEnv = process.env.ALLOW_LOCAL_FALLBACK === '1';

const shouldUseRemotePipeline = isVercel || forceRemotePipeline;
const allowLocalFallback = (!isVercel && !forceRemotePipeline) || allowLocalFallbackEnv;

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
} as const;

function buildRemoteEndpoints(protocol: string, host: string | null) {
  if (pipelineEndpointEnv && pipelineEndpointEnv.startsWith('http')) {
    return [pipelineEndpointEnv.replace(/\/+$/, '')];
  }

  if (!host) {
    return [] as string[];
  }

  const baseUrl = `${protocol}://${host}`;

  if (!pipelineEndpointEnv) {
    const apiDirectPython = `${baseUrl}/api/pipeline.py`.replace(/\/+$/, '');
    const pyCanonical = `${baseUrl}/py/pipeline`.replace(/\/+$/, '');
    const apiCanonical = `${baseUrl}/api/pipeline`.replace(/\/+$/, '');
    return Array.from(new Set([apiDirectPython, pyCanonical, apiCanonical]));
  }

  const normalizedPath = pipelineEndpointEnv.startsWith('/') ? pipelineEndpointEnv : `/${pipelineEndpointEnv}`;
  const absolute = `${baseUrl}${normalizedPath}`.replace(/\/+$/, '');

  if (absolute.endsWith('.py')) {
    return [absolute];
  }

  return Array.from(new Set([absolute, `${absolute}.py`]));
}

async function postWithRedirects(endpoint: string, payload: unknown, maxRedirects = 3) {
  let url = endpoint;
  for (let redirects = 0; redirects <= maxRedirects; redirects++) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
      redirect: 'manual',
      body: JSON.stringify(payload),
    });

    if (![301, 302, 303, 307, 308].includes(response.status)) {
      return { response, url } as const;
    }

    const location = response.headers.get('location');
    if (!location) {
      return { response, url } as const;
    }

    if (redirects === maxRedirects) {
      return { response, url } as const;
    }

    try {
      url = new URL(location, url).toString();
      console.warn(`Remote pipeline redirected to ${url}. Retrying POST.`);
    } catch (error) {
      console.warn(
        `Failed to resolve redirect location ${location}: ${error instanceof Error ? error.message : String(error)}`,
      );
      return { response, url } as const;
    }
  }

  throw new Error('Unexpected redirect handling fallthrough');
}

function withCors(init?: ResponseInit): ResponseInit {
  const headers = new Headers(init?.headers);
  Object.entries(corsHeaders).forEach(([key, value]) => {
    headers.set(key, value);
  });
  return { ...init, headers };
}

function jsonWithCors(body: unknown, init?: ResponseInit) {
  return NextResponse.json(body, withCors(init));
}

export async function OPTIONS() {
  return new Response(null, withCors({ status: 204, headers: { Allow: 'POST, OPTIONS' } }));
}

function runLocalPipeline(payload: unknown) {
  return new Promise<Response>((resolve) => {
    const child = spawn('python', ['api/run_local.py'], {
      cwd: process.cwd(),
      stdio: ['pipe', 'pipe', 'pipe'],
      env: process.env,
    });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    child.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    child.on('close', (code) => {
      if (code === 0) {
        try {
          const parsed = JSON.parse(stdout || '{}');
          if (parsed && typeof parsed === 'object' && parsed.error) {
            resolve(jsonWithCors(parsed, { status: 500 }));
            return;
          }
          resolve(jsonWithCors(parsed));
        } catch (error) {
          resolve(
            jsonWithCors(
              { error: 'Failed to parse local pipeline output', details: (error as Error).message },
              { status: 500 },
            ),
          );
        }
      } else {
        let message = 'Local pipeline failed';
        try {
          const parsedError = JSON.parse(stdout || '{}');
          message = parsedError.error || message;
        } catch (jsonError) {
          message = stderr || message;
        }

        resolve(
          jsonWithCors(
            {
              error: message,
              stderr,
            },
            { status: 500 },
          ),
        );
      }
    });

    child.stdin.end(JSON.stringify(payload));
  });
}

export async function POST(request: Request) {
  const payload = await request.json();

  if (shouldUseRemotePipeline) {
    const protocol = request.headers.get('x-forwarded-proto') ?? 'https';
    const host = request.headers.get('host');

    const hasAbsoluteEndpoint = Boolean(pipelineEndpointEnv && pipelineEndpointEnv.startsWith('http'));

    if (!host && !hasAbsoluteEndpoint) {
      return jsonWithCors({ error: 'Missing host header in request' }, { status: 502 });
    }

    const remoteEndpoints = buildRemoteEndpoints(protocol, host);
    if (!remoteEndpoints.length) {
      return jsonWithCors({ error: 'Unable to resolve remote pipeline endpoint' }, { status: 502 });
    }

    for (let index = 0; index < remoteEndpoints.length; index++) {
      const endpoint = remoteEndpoints[index];
      const isLastEndpoint = index === remoteEndpoints.length - 1;

      try {
        const { response, url: resolvedUrl } = await postWithRedirects(endpoint, payload);

        const text = await response.text();
        let body: unknown;
        try {
          body = text ? JSON.parse(text) : {};
        } catch (error) {
          body = { error: 'Upstream returned non-JSON payload', details: text, parserError: (error as Error).message };
        }

        if (!response.ok) {
          if (response.status === 405 && !isLastEndpoint) {
            console.warn(`Remote pipeline ${resolvedUrl} rejected POST; retrying fallback endpoint.`);
            continue;
          }

          if (allowLocalFallback) {
            console.warn(`Remote pipeline ${resolvedUrl} failed with status ${response.status}; using local pipeline.`);
            return runLocalPipeline(payload);
          }

          return jsonWithCors(
            {
              error: 'Remote pipeline request failed',
              status: response.status,
              details: typeof body === 'object' && body ? body : text,
            },
            { status: response.status },
          );
        }

        if (index > 0 || endpoint !== resolvedUrl) {
          console.warn(`Remote pipeline fallback succeeded via ${resolvedUrl}.`);
        }

        return jsonWithCors(body, { status: response.status });
      } catch (error) {
        if (!isLastEndpoint) {
          console.warn(
            `Remote pipeline request to ${endpoint} failed: ${
              error instanceof Error ? error.message : String(error)
            }. Trying fallback endpoint.`,
          );
          continue;
        }

        if (allowLocalFallback) {
          console.warn(`Remote pipeline ${endpoint} unreachable; using local pipeline.`);
          return runLocalPipeline(payload);
        }

        return jsonWithCors(
          {
            error: 'Failed to invoke Python handler',
            details: error instanceof Error ? error.message : String(error),
          },
          { status: 502 },
        );
      }
    }
  }

  return runLocalPipeline(payload);
}
