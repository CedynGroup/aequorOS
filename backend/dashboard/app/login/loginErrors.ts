import type { SignInResponse } from "next-auth/react";

export const INVALID_CREDENTIALS_MESSAGE = "Invalid email or password.";
export const SERVICE_UNAVAILABLE_MESSAGE =
  "Could not reach the AequorOS service, so your sign-in could not be checked. " +
  "This is not a problem with your credentials — try again shortly, or contact " +
  "your administrator if it persists.";
export const SIGN_IN_FAILED_MESSAGE =
  "Sign-in could not be completed. Please try again.";

export function loginErrorMessage(
  result: Pick<SignInResponse, "error" | "code">,
): string | null {
  if (!result.error) return null;
  if (
    result.error === "CredentialsSignin" &&
    result.code === "service_unavailable"
  ) {
    return SERVICE_UNAVAILABLE_MESSAGE;
  }
  if (result.error === "CredentialsSignin") {
    return INVALID_CREDENTIALS_MESSAGE;
  }
  return SIGN_IN_FAILED_MESSAGE;
}
