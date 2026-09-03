export function hasAccountAdministrationRole(
  roles: readonly string[],
): boolean {
  return roles.includes("account_admin") || roles.includes("admin");
}
