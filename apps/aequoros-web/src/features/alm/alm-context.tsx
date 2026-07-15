import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import {
  Alert,
  Button,
  Label,
  Panel,
  PanelHeader,
  Select,
  SelectItem,
} from "../../components/ui";
import { isApiError, riskApi, type TenantHeaders } from "../../lib/api";

export function useAlmWorkspace(
  tenant: TenantHeaders,
  bankId?: string,
  periodId?: string,
) {
  const banksQuery = useQuery({
    queryKey: ["alm-banks", tenant],
    queryFn: () => riskApi.listBanks(tenant),
  });
  const banks = banksQuery.data?.banks ?? [];
  const selectedBank = banks.find((bank) => bank.id === bankId) ?? banks[0];
  const periodsQuery = useQuery({
    queryKey: ["alm-periods", tenant, selectedBank?.id],
    queryFn: () =>
      riskApi.listBankReportingPeriods(tenant, selectedBank?.id ?? ""),
    enabled: Boolean(selectedBank),
  });
  // Periods arrive sorted by period end descending, so the first is the latest.
  const periods = periodsQuery.data?.periods ?? [];
  const selectedPeriod =
    periods.find((period) => period.id === periodId) ?? periods[0];

  return {
    banksQuery,
    periodsQuery,
    banks,
    periods,
    selectedBank,
    selectedPeriod,
  };
}

export type AlmWorkspace = ReturnType<typeof useAlmWorkspace>;

export function BankPeriodSelectors({
  workspace,
  onBank,
  onPeriod,
}: {
  workspace: AlmWorkspace;
  onBank: (bankId: string) => void;
  onPeriod: (periodId: string) => void;
}) {
  return (
    <>
      <Select
        ariaLabel="Bank"
        className="w-44 max-w-[28vw] sm:w-52"
        value={workspace.selectedBank?.id ?? ""}
        onValueChange={onBank}
        placeholder="Bank"
        disabled={!workspace.banks.length}
      >
        {workspace.banks.map((bank) => (
          <SelectItem key={bank.id} value={bank.id} className="max-w-80">
            {bank.name}
          </SelectItem>
        ))}
      </Select>
      <Select
        ariaLabel="Reporting period"
        className="w-36 max-w-[24vw] sm:w-44"
        value={workspace.selectedPeriod?.id ?? ""}
        onValueChange={onPeriod}
        placeholder="Reporting period"
        disabled={!workspace.periods.length}
      >
        {workspace.periods.map((period) => (
          <SelectItem key={period.id} value={period.id}>
            {period.label}
          </SelectItem>
        ))}
      </Select>
    </>
  );
}

export function NoBanksPanel({ tenant }: { tenant: TenantHeaders }) {
  const queryClient = useQueryClient();
  const seed = useMutation({
    mutationFn: () => riskApi.seedDemoBank(tenant),
    onSuccess: (summary) => {
      void queryClient.invalidateQueries({ queryKey: ["alm-banks"] });
      void queryClient.invalidateQueries({ queryKey: ["alm-periods"] });
      toast.success(
        `Seeded Sample Bank Ltd: ${summary.periods} reporting periods, ${summary.factCount} financial facts`,
      );
    },
    onError: (error) => {
      if (isApiError(error) && error.statusCode === 403) {
        toast.error(
          "Demo bank seeding is only available to the demo organization.",
        );
        return;
      }
      toast.error(
        isApiError(error) ? error.message : "Demo bank seeding failed.",
      );
    },
  });

  return (
    <Panel>
      <PanelHeader
        title="ALM Regulatory workspace"
        meta="Bank-level regulatory analytics for liquidity and capital"
      />
      <div className="space-y-3 p-4">
        <Alert title="No banks provisioned for this tenant">
          The ALM Regulatory console works on bank-level financial facts. Seed
          the demo Sample Bank Ltd dataset or provision a bank for this
          organization.
        </Alert>
        <Button
          size="sm"
          disabled={seed.isPending}
          onClick={() => seed.mutate()}
        >
          {seed.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          Seed Sample Bank Ltd (demo)
        </Button>
        <div>
          <Label>Demo tenant only</Label>
          <p className="mt-1 text-xs text-[rgb(var(--muted-foreground))]">
            Seeding is restricted to the demo organization; other tenants
            receive a 403.
          </p>
        </div>
      </div>
    </Panel>
  );
}
