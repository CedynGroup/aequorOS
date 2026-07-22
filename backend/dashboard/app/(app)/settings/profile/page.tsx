'use client';

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { Check, Monitor, Moon, Sun } from 'lucide-react';
import type { ProfileUpdateRequest } from '@aequoros/risk-service-api';

import { useUserProfile } from '@/components/profile/ProfileProvider';
import {
  useTheme,
  type ThemePreference,
} from '@/components/shell/ThemeProvider';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import PageHeader from '@/components/ui/PageHeader';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { avatarColor, initialsFrom, roleLabel } from '@/lib/api/identity';

type ProfileForm = {
  displayName: string;
  jobTitle: string;
  locale: string;
  timezone: string;
};

type ProfileField = keyof ProfileForm;
type SaveStatus = 'saved' | 'newer-edits' | null;

const EMPTY_FORM: ProfileForm = {
  displayName: '',
  jobTitle: '',
  locale: '',
  timezone: '',
};

const THEME_OPTIONS: {
  value: ThemePreference;
  label: string;
  description: string;
  Icon: typeof Sun;
}[] = [
  {
    value: 'light',
    label: 'Light',
    description: 'Use the light palette',
    Icon: Sun,
  },
  {
    value: 'dark',
    label: 'Dark',
    description: 'Use the dark palette',
    Icon: Moon,
  },
  {
    value: 'system',
    label: 'System',
    description: 'Follow this device',
    Icon: Monitor,
  },
];

const LOCALES = ['en-GH', 'en-GB', 'en-US', 'fr-FR', 'fr-CA', 'pt-BR'];
const TIMEZONES = [
  'Africa/Accra',
  'Africa/Lagos',
  'Africa/Nairobi',
  'Africa/Johannesburg',
  'Europe/London',
  'America/New_York',
  'UTC',
];

const INPUT_CLASS =
  'w-full px-3 py-2.5 border border-border rounded-md bg-surface text-body text-navy placeholder:text-slate-light focus:outline-none focus:ring-2 focus:ring-action/25 focus:border-action';

function optional(value: string): string | null {
  return value.trim() || null;
}

function buildProfileUpdates(
  form: ProfileForm,
  fields: readonly ProfileField[],
): ProfileUpdateRequest {
  const updates: ProfileUpdateRequest = {};
  for (const field of fields) {
    switch (field) {
      case 'displayName':
        updates.displayName = optional(form.displayName);
        break;
      case 'jobTitle':
        updates.jobTitle = optional(form.jobTitle);
        break;
      case 'locale':
        updates.locale = optional(form.locale);
        break;
      case 'timezone':
        updates.timezone = optional(form.timezone);
        break;
    }
  }
  return updates;
}

export default function ProfilePage() {
  const { profile, isLoading, error, updateProfile, isSaving, refetch } =
    useUserProfile();
  const { theme, setTheme } = useTheme();
  const [form, setForm] = useState<ProfileForm>(EMPTY_FORM);
  const latestForm = useRef<ProfileForm>(EMPTY_FORM);
  const dirtyFields = useRef(new Set<ProfileField>());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(null);

  useEffect(() => {
    if (!profile) return;
    const serverForm: ProfileForm = {
      displayName: profile.displayName ?? '',
      jobTitle: profile.jobTitle ?? '',
      locale: profile.locale ?? '',
      timezone: profile.timezone ?? '',
    };
    setForm((current) => {
      const next = {
        displayName: dirtyFields.current.has('displayName')
          ? current.displayName
          : serverForm.displayName,
        jobTitle: dirtyFields.current.has('jobTitle')
          ? current.jobTitle
          : serverForm.jobTitle,
        locale: dirtyFields.current.has('locale')
          ? current.locale
          : serverForm.locale,
        timezone: dirtyFields.current.has('timezone')
          ? current.timezone
          : serverForm.timezone,
      };
      latestForm.current = next;
      return next;
    });
  }, [profile]);

  function updateField<Field extends ProfileField>(
    field: Field,
    value: ProfileForm[Field],
  ) {
    dirtyFields.current.add(field);
    setSaveStatus(null);
    const next = {
      ...latestForm.current,
      [field]: value,
    };
    latestForm.current = next;
    setForm(next);
  }

  function reconcileSavedForm(
    submitted: ProfileForm,
    submittedFields: readonly ProfileField[],
    savedProfile: Awaited<ReturnType<typeof updateProfile>>,
  ): boolean {
    const savedForm: ProfileForm = {
      displayName: savedProfile.displayName ?? '',
      jobTitle: savedProfile.jobTitle ?? '',
      locale: savedProfile.locale ?? '',
      timezone: savedProfile.timezone ?? '',
    };
    const reconciled = { ...latestForm.current };
    function reconcileField<Field extends ProfileField>(field: Field) {
      if (latestForm.current[field] === submitted[field]) {
        reconciled[field] = savedForm[field];
        dirtyFields.current.delete(field);
      }
    }
    submittedFields.forEach(reconcileField);
    latestForm.current = reconciled;
    setForm(reconciled);
    return dirtyFields.current.size > 0;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveStatus(null);
    setSaveError(null);
    const submitted = { ...latestForm.current };
    const submittedFields = Array.from(dirtyFields.current);
    if (submittedFields.length === 0) return;
    try {
      const savedProfile = await updateProfile(
        buildProfileUpdates(submitted, submittedFields),
      );
      const hasNewerEdits = reconcileSavedForm(
        submitted,
        submittedFields,
        savedProfile,
      );
      setSaveStatus(hasNewerEdits ? 'newer-edits' : 'saved');
    } catch (updateError) {
      setSaveError(
        updateError instanceof Error
          ? updateError.message
          : 'Could not save your profile.',
      );
    }
  }

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Settings', href: '/settings' },
          { label: 'Profile & preferences' },
        ]}
        title="Profile & preferences"
        subtitle="Manage how your personal details and dashboard appearance are displayed."
      />

      <div className="px-4 md:px-8 py-6 max-w-5xl">
        {isLoading && !profile ? (
          <Card>
            <CardBody className="space-y-4">
              <SkeletonLine className="w-48" />
              <SkeletonLine className="w-full" />
              <SkeletonLine className="w-3/4" />
            </CardBody>
          </Card>
        ) : error && !profile ? (
          <Card>
            <CardBody>
              <p className="text-body text-danger">
                Could not load your profile. {error.message}
              </p>
              <button
                type="button"
                onClick={() => void refetch()}
                className="mt-4 px-4 py-2 rounded bg-action text-white text-caption font-medium hover:bg-action/90"
              >
                Try again
              </button>
            </CardBody>
          </Card>
        ) : profile ? (
          <form onSubmit={onSubmit} className="space-y-6">
            <Card>
              <CardHeader
                title="Personal details"
                subtitle="These details identify you throughout AequorOS."
              />
              <CardBody>
                <div className="flex items-center gap-4 pb-6 mb-6 border-b border-border-light">
                  <span
                    className="inline-flex items-center justify-center w-14 h-14 rounded-full text-white text-h3 font-semibold shrink-0"
                    style={{ backgroundColor: avatarColor(profile.userId) }}
                    aria-label="Profile initials"
                  >
                    {initialsFrom(
                      form.displayName || profile.email || 'Signed in',
                    )}
                  </span>
                  <div className="min-w-0">
                    <p className="text-body font-medium text-navy truncate">
                      {form.displayName || profile.email}
                    </p>
                    <p className="text-caption text-slate truncate">
                      {profile.email} · {roleLabel(profile.role)}
                    </p>
                    <p className="mt-1 text-micro text-slate">
                      Your avatar is generated from your initials.
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <Field label="Display name" htmlFor="display-name">
                    <input
                      id="display-name"
                      value={form.displayName}
                      maxLength={255}
                      autoComplete="name"
                      onChange={(event) =>
                        updateField('displayName', event.target.value)
                      }
                      className={INPUT_CLASS}
                      placeholder="Jane Mensah"
                    />
                  </Field>
                  <Field label="Job title" htmlFor="job-title">
                    <input
                      id="job-title"
                      value={form.jobTitle}
                      maxLength={255}
                      autoComplete="organization-title"
                      onChange={(event) =>
                        updateField('jobTitle', event.target.value)
                      }
                      className={INPUT_CLASS}
                      placeholder="Treasury Analyst"
                    />
                  </Field>
                  <Field
                    label="Email"
                    htmlFor="email"
                    hint="Managed by your organization"
                  >
                    <input
                      id="email"
                      value={profile.email}
                      readOnly
                      aria-readonly="true"
                      className={`${INPUT_CLASS} opacity-70 cursor-not-allowed`}
                    />
                  </Field>
                  <Field
                    label="Locale"
                    htmlFor="locale"
                    hint="BCP-47 language tag"
                  >
                    <input
                      id="locale"
                      value={form.locale}
                      maxLength={35}
                      list="profile-locales"
                      pattern="[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*"
                      onChange={(event) =>
                        updateField('locale', event.target.value)
                      }
                      className={INPUT_CLASS}
                      placeholder="en-GH"
                    />
                    <datalist id="profile-locales">
                      {LOCALES.map((locale) => (
                        <option key={locale} value={locale} />
                      ))}
                    </datalist>
                  </Field>
                  <Field
                    label="Time zone"
                    htmlFor="timezone"
                    hint="IANA time zone name"
                  >
                    <input
                      id="timezone"
                      value={form.timezone}
                      maxLength={255}
                      list="profile-timezones"
                      onChange={(event) =>
                        updateField('timezone', event.target.value)
                      }
                      className={INPUT_CLASS}
                      placeholder="Africa/Accra"
                    />
                    <datalist id="profile-timezones">
                      {TIMEZONES.map((timezone) => (
                        <option key={timezone} value={timezone} />
                      ))}
                    </datalist>
                  </Field>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Appearance"
                subtitle="This preference follows your account across browsers."
              />
              <CardBody>
                <div
                  role="radiogroup"
                  aria-label="Theme preference"
                  className="grid grid-cols-1 sm:grid-cols-3 gap-3"
                >
                  {THEME_OPTIONS.map(({ value, label, description, Icon }) => {
                    const selected = theme === value;
                    return (
                      <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => setTheme(value)}
                        className={`flex items-center gap-3 p-4 rounded-md border text-left transition-colors ${
                          selected
                            ? 'border-action bg-action-light text-navy'
                            : 'border-border-light bg-surface hover:border-action/40 text-navy'
                        }`}
                      >
                        <Icon
                          size={18}
                          className="text-action shrink-0"
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block text-body font-medium">
                            {label}
                          </span>
                          <span className="block text-caption text-slate">
                            {description}
                          </span>
                        </span>
                        {selected && (
                          <Check
                            size={16}
                            className="text-action shrink-0"
                            aria-hidden
                          />
                        )}
                      </button>
                    );
                  })}
                </div>
              </CardBody>
            </Card>

            <div className="flex items-center justify-end gap-4">
              {saveError && (
                <p role="alert" className="text-caption text-danger">
                  {saveError}
                </p>
              )}
              {saveStatus === 'saved' && !saveError && (
                <p role="status" className="text-caption text-success">
                  Profile saved.
                </p>
              )}
              {saveStatus === 'newer-edits' && !saveError && (
                <p role="status" className="text-caption text-slate">
                  Profile saved. Newer edits remain unsaved.
                </p>
              )}
              <button
                type="submit"
                disabled={isSaving}
                className="px-5 py-2.5 rounded bg-action text-white text-body font-medium hover:bg-action/90 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSaving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </form>
        ) : null}
      </div>
    </>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="block min-w-0">
      <span className="flex items-center justify-between gap-3 mb-1.5">
        <span className="text-caption font-medium text-navy">{label}</span>
        {hint && <span className="text-micro text-slate">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
