'use client';

import { FormEvent, useCallback, useMemo, useState } from 'react';

type Totals = {
  careers_processed: number;
  job_urls_found: number;
  rows_appended: number;
  duplicates: number;
  errors: number;
};

type PipelineError = {
  scope?: string | null;
  url?: string | null;
  message: string;
};

type ApiResponse = {
  totals?: Totals;
  dryRun?: boolean;
  errors?: PipelineError[];
  error?: string;
  details?: string;
  stderr?: string;
};

const parseUrl = (value: string) => {
  try {
    return new URL(value);
  } catch (error) {
    return null;
  }
};

const emptyTotals: Totals = {
  careers_processed: 0,
  job_urls_found: 0,
  rows_appended: 0,
  duplicates: 0,
  errors: 0,
};

export default function ScrapeForm() {
  const [url, setUrl] = useState('');
  const [dryRun, setDryRun] = useState(true);
  const [maxJobs, setMaxJobs] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [totals, setTotals] = useState<Totals | null>(null);
  const [errorDetails, setErrorDetails] = useState<string[] | null>(null);

  const isSubmitDisabled = useMemo(() => {
    if (loading) {
      return true;
    }
    if (!url.trim()) {
      return true;
    }
    return !parseUrl(url.trim());
  }, [loading, url]);

  const onDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const droppedUrl = event.dataTransfer.getData('text/uri-list') || event.dataTransfer.getData('text/plain');
    if (!droppedUrl) {
      return;
    }
    const parsed = parseUrl(droppedUrl.trim());
    if (parsed) {
      setUrl(parsed.toString());
      setStatus('URL detected from drop. Ready to submit.');
      setError(null);
    } else {
      setError('Drag a valid http(s) URL.');
    }
  }, []);

  const onDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
  }, []);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmed = url.trim();
      const parsed = parseUrl(trimmed);
      if (!parsed) {
        setError('Enter a valid http(s) URL.');
        return;
      }

      setLoading(true);
      setError(null);
      setStatus('Scraping jobs… This can take up to a minute for large boards.');
      setTotals(null);
      setErrorDetails(null);

      const payload: Record<string, unknown> = {
        url: parsed.toString(),
        dryRun,
      };

      if (maxJobs.trim()) {
        const numeric = Number(maxJobs);
        if (Number.isNaN(numeric) || numeric <= 0) {
          setLoading(false);
          setError('Max jobs must be a positive number.');
          setStatus(null);
          return;
        }
        payload.maxJobs = numeric;
      }

      try {
        const response = await fetch('/api/run', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        });

        const data = (await response.json()) as ApiResponse;

        const normalizeErrorDetails = (incoming: unknown): string[] => {
          if (!Array.isArray(incoming)) {
            return [];
          }
          return incoming
            .map((entry) => {
              if (entry && typeof entry === 'object') {
                const shaped = entry as { message?: unknown; url?: unknown };
                const message = typeof shaped.message === 'string' ? shaped.message : null;
                if (!message) {
                  return null;
                }
                const urlText = typeof shaped.url === 'string' && shaped.url ? ` (${shaped.url})` : '';
                return `${message}${urlText}`;
              }
              return typeof entry === 'string' ? entry : null;
            })
            .filter((value): value is string => Boolean(value));
        };

        if (!response.ok) {
          const message = data.error || 'Scrape failed. Check dev console for details.';
          if (data.stderr) {
            console.error(data.stderr);
          }
          const details = normalizeErrorDetails(data.errors);
          setErrorDetails(details.length ? details : null);
          if (data.totals) {
            setTotals(data.totals);
          } else {
            setTotals(null);
          }
          setError(message);
          setStatus(null);
          return;
        }

        setTotals(data.totals ?? emptyTotals);
        const statusMessage = (data.dryRun ?? dryRun)
          ? 'Dry run complete. Totals saved locally.'
          : 'Pipeline run complete.';
        setStatus(statusMessage);
        const details = normalizeErrorDetails(data.errors);
        setErrorDetails(details.length ? details : null);
      } catch (requestError) {
        setError(requestError instanceof Error ? requestError.message : 'Unexpected network error.');
        setStatus(null);
        setTotals(null);
        setErrorDetails(null);
      } finally {
        setLoading(false);
      }
    },
    [dryRun, maxJobs, url],
  );

  return (
    <div className="scrape-container" onDrop={onDrop} onDragOver={onDragOver}>
      <section className="scrape-card" aria-labelledby="scraper-heading">
        <header className="scrape-card__header">
          <h1 id="scraper-heading">Industrion Careers Scraper</h1>
          <p>Drop or paste a careers page URL to extract job listings using the Industrion pipeline.</p>
        </header>
        <form className="scrape-form" onSubmit={handleSubmit}>
          <label className="form-field">
            <span className="form-label">Careers page URL</span>
            <div className="url-drop">
              <input
                autoComplete="url"
                className="url-input"
                name="url"
                onChange={(event) => setUrl(event.currentTarget.value)}
                placeholder="https://company.com/careers"
                type="url"
                value={url}
                disabled={loading}
              />
              <span className="url-hint">Drag a link here or paste it above</span>
            </div>
          </label>

          <fieldset className="form-grid">
            <label className="form-field checkbox-field">
              <input
                checked={dryRun}
                disabled={loading}
                onChange={(event) => setDryRun(event.currentTarget.checked)}
                type="checkbox"
              />
              <span>
                Dry run mode
                <small>Outputs CSV totals without writing to Google Sheets.</small>
              </span>
            </label>

            <label className="form-field">
              <span className="form-label">Max jobs (optional)</span>
              <input
                className="number-input"
                inputMode="numeric"
                min={1}
                name="maxJobs"
                onChange={(event) => setMaxJobs(event.currentTarget.value)}
                placeholder="Limit results"
                type="number"
                value={maxJobs}
                disabled={loading}
              />
            </label>
          </fieldset>

          <button className="submit-button" disabled={isSubmitDisabled} type="submit">
            {loading ? 'Running pipeline…' : 'Run scraper'}
          </button>
          <p className="terms-note">Configure Firecrawl and OpenRouter keys via Vercel environment variables before running live.</p>
        </form>
      </section>

      <aside className="status-panel" aria-live="polite">
        {status && <p className="status-message">{status}</p>}
        {error && <p className="status-error">{error}</p>}
        {errorDetails && errorDetails.length > 0 && (
          <ul className="status-error-details">
            {errorDetails.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        )}
        {totals && (
          <dl className="totals-grid">
            {Object.entries(totals).map(([key, value]) => (
              <div key={key} className="totals-item">
                <dt>{key.replace(/_/g, ' ')}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
        {!status && !error && !totals && <p className="status-placeholder">Drop a link to get started.</p>}
      </aside>
    </div>
  );
}
