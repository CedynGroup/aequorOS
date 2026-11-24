/** What a server action reports back to the form that called it. */
export type ActionState = {
  status: "idle" | "success" | "error";
  message: string;
  /** Changes on every result so a repeated message is announced again. */
  at: number;
  /** Clear the form's fields after success. */
  reset?: boolean;
};

export const IDLE: ActionState = { status: "idle", message: "", at: 0 };

export type FormAction = (state: ActionState, formData: FormData) => Promise<ActionState>;

/** Success notices shown after an action redirects; the URL carries only the key. */
export const NOTICES = {
  accepted: "Filing accepted. The institution has been told.",
  assigned: "Filing assigned to you.",
  resubmission_requested: "Resubmission requested. The institution has been sent the request.",
  query_sent: "Query sent to the institution.",
  message_sent: "Message sent to the institution.",
  query_closed: "Query closed.",
  note_added: "Supervisory note added.",
  return_approved: "Approval recorded. The version publishes when the other approval is given.",
  return_published: "Version approved and published.",
  return_returned: "Draft returned to the drafter.",
  draft_created: "Draft saved. It now needs two approvals from other officers.",
  access_approved: "Operator session approved. It ends automatically at the limit.",
  access_denied: "Operator request denied.",
} as const;

export type NoticeKey = keyof typeof NOTICES;

export function isNoticeKey(value: string | undefined): value is NoticeKey {
  return Boolean(value && Object.prototype.hasOwnProperty.call(NOTICES, value));
}
