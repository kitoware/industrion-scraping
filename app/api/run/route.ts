import { NextResponse } from 'next/server';
import { spawn } from 'node:child_process';

const isProd = process.env.NODE_ENV === 'production';

export async function POST(request: Request) {
  if (isProd) {
    return NextResponse.json({ error: 'Python handler serves /api/run in production.' }, { status: 404 });
  }

  const payload = await request.json();

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
            resolve(NextResponse.json(parsed, { status: 500 }));
            return;
          }
          resolve(NextResponse.json(parsed));
        } catch (error) {
          resolve(
            NextResponse.json(
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
          NextResponse.json(
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
