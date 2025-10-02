import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Industrion Jobs Scraper',
  description: 'Submit a careers page URL and extract job listings with the Industrion scraping pipeline.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
