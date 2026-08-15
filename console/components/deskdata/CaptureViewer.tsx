'use client';

import { useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import {
  getDeskCaptureContent,
  getDeskCaptureSnippet,
  toApiError,
  type ApiError,
  type DeskCaptureContentView,
  type DeskObservationSnippet,
} from '@/lib/api';
import { fmtDate } from '@/lib/format';
import {
  Button,
  Chip,
  ErrorPanel,
  Field,
  Input,
  Modal,
  MonoId,
  SkeletonRows,
  StatusChip,
} from '@/components/ui';

/**
 * Accessible capture-content viewer (replaces the old hand-rolled, focus-leaky
 * modal in /desk/sources). On open it loads the decoded silver payload
 * (`getDeskCaptureContent`) for metadata + full text; the value search runs the
 * lighter field-level window endpoint (`getDeskCaptureSnippet`) so an operator
 * can jump straight to the digit that became an observation. All the overlay
 * behavior (focus trap, ESC, scroll-lock, restore) comes from the foundation
 * Modal.
 */
export function CaptureViewer({
  captureId,
  onClose,
}: {
  captureId: string | null;
  onClose: () => void;
}) {
  const [content, setContent] = useState<DeskCaptureContentView | null>(null);
  const [contentError, setContentError] = useState<ApiError | null>(null);
  const [contentLoading, setContentLoading] = useState(false);

  const [needle, setNeedle] = useState('');
  const [snippet, setSnippet] = useState<DeskObservationSnippet | null>(null);
  const [snippetError, setSnippetError] = useState<ApiError | null>(null);
  const [snippetBusy, setSnippetBusy] = useState(false);

  // A monotonic token guards against a stale response landing after the
  // operator has moved to a different capture (or closed the viewer).
  const reqToken = useRef(0);

  useEffect(() => {
    setSnippet(null);
    setSnippetError(null);
    setNeedle('');
    if (!captureId) {
      setContent(null);
      setContentError(null);
      return;
    }
    const token = ++reqToken.current;
    setContentLoading(true);
    setContentError(null);
    getDeskCaptureContent(captureId)
      .then((view) => {
        if (token !== reqToken.current) return;
        setContent(view);
        setContentLoading(false);
      })
      .catch((err: unknown) => {
        if (token !== reqToken.current) return;
        setContentError(toApiError(err));
        setContent(null);
        setContentLoading(false);
      });
  }, [captureId]);

  async function runSearch() {
    if (!captureId || needle.trim() === '') return;
    const token = ++reqToken.current;
    setSnippetBusy(true);
    setSnippetError(null);
    try {
      const view = await getDeskCaptureSnippet(captureId, needle.trim());
      if (token !== reqToken.current) return;
      setSnippet(view);
    } catch (err) {
      if (token !== reqToken.current) return;
      setSnippetError(toApiError(err));
      setSnippet(null);
    }
    if (token === reqToken.current) setSnippetBusy(false);
  }

  return (
    <Modal
      open={captureId !== null}
      onClose={onClose}
      size="xl"
      title="Capture content"
      description={captureId ? <MonoId id={captureId} /> : undefined}
    >
      {contentLoading && <SkeletonRows rows={6} />}
      {contentError && <ErrorPanel error={contentError} context="Loading capture content" />}

      {content && (
        <div className="space-y-4">
          {/* metadata */}
          <div className="flex flex-wrap items-center gap-2">
            <Chip mono>{content.source_key}</Chip>
            <Chip>{content.kind}</Chip>
            <StatusChip value={content.status} />
            <Chip tone={content.content_available ? 'ok' : 'warn'}>
              {content.content_available ? `${content.content_bytes} bytes` : 'content not inline'}
            </Chip>
            {content.truncated && <Chip tone="warn">truncated</Chip>}
            <span className="text-caption text-slate">as of {fmtDate(content.as_of_date)}</span>
          </div>

          {content.source_url && (
            <a
              href={content.source_url}
              target="_blank"
              rel="noreferrer"
              className="block truncate text-caption text-action hover:underline"
            >
              {content.source_url}
            </a>
          )}

          {content.content_omitted && (
            <p className="rounded border border-warning/40 bg-warning-light/40 p-2.5 text-caption text-warning">
              {content.content_omitted}
            </p>
          )}

          {/* field-level value search (getDeskCaptureSnippet) */}
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void runSearch();
            }}
          >
            <Field label="Find value (field-level snippet)" className="min-w-[12rem] flex-1">
              <Input
                value={needle}
                onChange={(e) => setNeedle(e.target.value)}
                placeholder="e.g. 15.00"
                className="font-mono"
              />
            </Field>
            <Button type="submit" icon={<Search size={14} />} loading={snippetBusy} disabled={needle.trim() === ''}>
              Search
            </Button>
          </form>

          {snippetError && <ErrorPanel error={snippetError} context="Searching the capture" />}
          {snippet && (
            <div>
              <h3 className="mb-1 text-body font-medium text-navy">
                Snippet around <span className="font-mono">{snippet.needle}</span>
              </h3>
              {snippet.snippet ? (
                <pre className="whitespace-pre-wrap rounded border border-action/30 bg-action-light/30 p-3 font-mono text-caption text-ink">
                  {snippet.snippet}
                </pre>
              ) : (
                <p className="text-caption text-slate">
                  {snippet.hint ?? 'Value not found in the stored capture text.'}
                </p>
              )}
            </div>
          )}

          {/* full decoded text */}
          {content.text != null && (
            <div>
              <h3 className="mb-1 text-body font-medium text-navy">Full text</h3>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border-light bg-surface p-3 font-mono text-micro text-ink">
                {content.text}
              </pre>
            </div>
          )}

          {!content.content_available && content.text == null && (
            <p className="text-caption text-slate">
              Raw bytes are not stored inline (over the size cap). Use the source URL, or re-capture
              with a smaller artifact.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
