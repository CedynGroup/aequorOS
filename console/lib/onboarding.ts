import { getHealth, type ProvisionTenantRequest } from "./api";

const RETRY_DELAYS = [500, 1000, 2000, 4000];

export function watchBankKeyRequirement(
  onRequired: (required: boolean) => void,
  recoveryTarget: EventTarget,
): () => void {
  let disposed = false;
  let resolved = false;
  let pending = false;
  let retries = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function load() {
    if (disposed || resolved || pending) return;
    clearTimeout(timer);
    pending = true;
    try {
      const health = await getHealth();
      if (!disposed) {
        resolved = true;
        onRequired(health.bank_key_required);
      }
    } catch {
      if (!disposed && retries < RETRY_DELAYS.length) {
        timer = setTimeout(() => void load(), RETRY_DELAYS[retries++]);
      }
    } finally {
      pending = false;
    }
  }

  function recover() {
    if (resolved || pending) return;
    retries = 0;
    void load();
  }

  recoveryTarget.addEventListener("online", recover);
  recoveryTarget.addEventListener("focus", recover);
  void load();

  return () => {
    disposed = true;
    clearTimeout(timer);
    recoveryTarget.removeEventListener("online", recover);
    recoveryTarget.removeEventListener("focus", recover);
  };
}

export function onboardingKeyState(
  bankKeyRequired: boolean,
  key: NonNullable<ProvisionTenantRequest["encryption_key"]>,
) {
  const keyId = key.key_id.trim();
  const region = key.region.trim();
  const required =
    bankKeyRequired ||
    keyId !== "" ||
    region !== "" ||
    key.owner_account.trim() !== "";
  return {
    required,
    complete:
      !required ||
      (keyId !== "" && region !== "" && /^[0-9]{12}$/.test(key.owner_account)),
    encryptionKey: required ? { ...key, key_id: keyId, region } : null,
  };
}
