import type { SsoAccessRequestApproveRoleEnum } from "@aequoros/risk-service-api";

type SsoApprovalRoleEnum = Readonly<{
  AccountAdmin: SsoAccessRequestApproveRoleEnum;
  Approver: SsoAccessRequestApproveRoleEnum;
  Analyst: SsoAccessRequestApproveRoleEnum;
  Viewer: SsoAccessRequestApproveRoleEnum;
}>;

export function hasAccountAdministrationRole(
  roles: readonly string[],
): boolean {
  return roles.includes("account_admin") || roles.includes("admin");
}

export function ssoApprovalRoleOptions(
  roleEnum: SsoApprovalRoleEnum,
): readonly SsoAccessRequestApproveRoleEnum[] {
  return [
    roleEnum.Viewer,
    roleEnum.Analyst,
    roleEnum.Approver,
    roleEnum.AccountAdmin,
  ];
}
