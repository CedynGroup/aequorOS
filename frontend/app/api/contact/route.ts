import { NextResponse } from 'next/server';
import { Resend } from 'resend';

export const runtime = 'nodejs';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const DEFAULT_FROM = 'AequorOS <onboarding@resend.dev>';
const DEFAULT_TO = 'eric@aequoros.com';

type Body = {
  name?: unknown;
  email?: unknown;
  organization?: unknown;
  role?: unknown;
  message?: unknown;
  _gotcha?: unknown;
};

function str(v: unknown, max: number): string {
  if (typeof v !== 'string') return '';
  return v.trim().slice(0, max);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export async function POST(request: Request) {
  if (!process.env.RESEND_API_KEY) {
    return NextResponse.json(
      {
        error:
          'Contact system is not yet configured. Please email eric@aequoros.com directly.',
      },
      { status: 503 }
    );
  }

  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json({ error: 'Invalid request.' }, { status: 400 });
  }

  // Honeypot — silently accept, never deliver
  if (typeof body._gotcha === 'string' && body._gotcha.length > 0) {
    return NextResponse.json({ ok: true });
  }

  const name = str(body.name, 100);
  const email = str(body.email, 200);
  const organization = str(body.organization, 150);
  const role = str(body.role, 50);
  const message = str(body.message, 5000);

  if (!name || !email || !role) {
    return NextResponse.json(
      { error: 'Name, email, and role are required.' },
      { status: 400 }
    );
  }

  if (!EMAIL_RE.test(email)) {
    return NextResponse.json(
      { error: 'Please enter a valid email address.' },
      { status: 400 }
    );
  }

  const resend = new Resend(process.env.RESEND_API_KEY);

  const from = process.env.RESEND_FROM_EMAIL || DEFAULT_FROM;
  const to = process.env.RESEND_TO_EMAIL || DEFAULT_TO;
  const subject = `New demo request from aequoros.com — ${role}`;

  const text = [
    `Name: ${name}`,
    `Email: ${email}`,
    organization ? `Organization: ${organization}` : null,
    `Role: ${role}`,
    '',
    message || '(no message provided)',
  ]
    .filter((line) => line !== null)
    .join('\n');

  const orgRow = organization
    ? `<tr><td style="padding: 4px 12px 4px 0; color: #64748B;">Organization</td><td style="padding: 4px 0;">${escapeHtml(organization)}</td></tr>`
    : '';
  const messageBlock = message
    ? `<div style="border-left: 4px solid #4FC3F7; padding: 8px 16px; background: #F8FAFC; white-space: pre-wrap;">${escapeHtml(message)}</div>`
    : `<div style="color: #64748B; font-style: italic;">No message provided.</div>`;

  const html = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif; color: #1A202C; line-height: 1.6;">
      <h2 style="color: #1E2761; font-family: Georgia, serif; margin-bottom: 16px;">New demo request from aequoros.com</h2>
      <table style="border-collapse: collapse; margin-bottom: 16px;">
        <tr><td style="padding: 4px 12px 4px 0; color: #64748B;">Name</td><td style="padding: 4px 0;"><strong>${escapeHtml(name)}</strong></td></tr>
        <tr><td style="padding: 4px 12px 4px 0; color: #64748B;">Email</td><td style="padding: 4px 0;"><a href="mailto:${escapeHtml(email)}" style="color: #4FC3F7; text-decoration: none;">${escapeHtml(email)}</a></td></tr>
        ${orgRow}
        <tr><td style="padding: 4px 12px 4px 0; color: #64748B;">Role</td><td style="padding: 4px 0;">${escapeHtml(role)}</td></tr>
      </table>
      ${messageBlock}
    </div>
  `.trim();

  try {
    const { error } = await resend.emails.send({
      from,
      to,
      replyTo: email,
      subject,
      text,
      html,
    });

    if (error) {
      console.error('Resend error:', error);
      return NextResponse.json(
        { error: 'Failed to send message. Please email eric@aequoros.com directly.' },
        { status: 502 }
      );
    }

    return NextResponse.json({ ok: true });
  } catch (err) {
    console.error('Contact route error:', err);
    return NextResponse.json(
      { error: 'Unexpected error. Please email eric@aequoros.com directly.' },
      { status: 500 }
    );
  }
}
