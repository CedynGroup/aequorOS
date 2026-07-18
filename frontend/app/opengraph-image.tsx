import { ImageResponse } from 'next/og';

export const runtime = 'edge';
export const alt =
  'AequorOS — Treasury and ALM infrastructure for African banks';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

// Branded, self-generated social card so previews are never broken. Colors
// mirror the site's design tokens (navy-deep / accent / ice-blue).
export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          background: '#0F1845',
          padding: '80px',
          fontFamily: 'Georgia, serif',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div
            style={{
              width: '20px',
              height: '56px',
              background: '#4FC3F7',
              borderRadius: '4px',
            }}
          />
          <div
            style={{ color: '#FFFFFF', fontSize: '44px', fontWeight: 700 }}
          >
            AequorOS
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '28px' }}>
          <div
            style={{
              color: '#FFFFFF',
              fontSize: '72px',
              fontWeight: 700,
              lineHeight: 1.05,
              maxWidth: '960px',
            }}
          >
            Treasury and ALM infrastructure for African banks.
          </div>
          <div style={{ color: '#CADCFC', fontSize: '30px', maxWidth: '900px' }}>
            Connected to your core. Auditable end to end. MVP live — onboarding
            pilot banks.
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
