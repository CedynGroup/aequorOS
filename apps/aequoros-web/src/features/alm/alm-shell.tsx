import {
  Activity,
  ArrowLeftRight,
  FileCheck2,
  Gauge,
  Landmark,
  Layers,
  LineChart,
  PieChart,
  RefreshCw,
  Scale,
  ShieldCheck,
  SlidersHorizontal,
  TrendingDown,
  Waves,
} from "lucide-react";

import { Button, Input, Tooltip } from "../../components/ui";
import type { AlmTab, ConsoleMode } from "../../lib/constants";
import { cn } from "../../lib/utils";
import { BankPeriodSelectors, type AlmWorkspace } from "./alm-context";

export function ModeSwitch({
  mode,
  onMode,
}: {
  mode: ConsoleMode;
  onMode: (mode: ConsoleMode) => void;
}) {
  return (
    <div className="mb-3 grid grid-cols-2 gap-1 rounded-md bg-[rgb(var(--muted))] p-1">
      {(
        [
          ["cases", "Risk Console"],
          ["alm", "ALM Regulatory"],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          onClick={() => onMode(value)}
          className={cn(
            "h-7 truncate whitespace-nowrap rounded px-1 text-[10px] font-medium text-[rgb(var(--muted-foreground))]",
            mode === value &&
              "bg-[rgb(var(--surface))] text-[rgb(var(--foreground))] shadow-sm",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

type AlmNavGroup = {
  label: string | null;
  items: Array<{ tab: AlmTab; label: string; icon: typeof Gauge }>;
};

const navGroups: AlmNavGroup[] = [
  {
    label: null,
    items: [{ tab: "overview", label: "ALM Overview", icon: Gauge }],
  },
  {
    label: "Liquidity Risk",
    items: [
      { tab: "lcr", label: "LCR", icon: Waves },
      { tab: "nsfr", label: "NSFR", icon: Scale },
      { tab: "cashflow", label: "Cash Flow", icon: ArrowLeftRight },
      { tab: "liq-stress", label: "Stress", icon: Activity },
    ],
  },
  {
    label: "Basel Capital",
    items: [
      { tab: "capital", label: "Dashboard", icon: Landmark },
      { tab: "rwa", label: "RWA", icon: PieChart },
      { tab: "structure", label: "Structure", icon: Layers },
      { tab: "capital-stress", label: "Stress", icon: TrendingDown },
    ],
  },
  {
    label: "Forecasting",
    items: [
      { tab: "forecast", label: "Forecast", icon: LineChart },
      { tab: "optimizer", label: "Optimizer", icon: SlidersHorizontal },
      { tab: "whatif", label: "What-If", icon: ArrowLeftRight },
    ],
  },
  {
    label: "Submissions",
    items: [{ tab: "submissions", label: "Submissions", icon: FileCheck2 }],
  },
];

export function AlmSidebar({
  activeTab,
  onTab,
  mode,
  onMode,
}: {
  activeTab: AlmTab;
  onTab: (tab: AlmTab) => void;
  mode: ConsoleMode;
  onMode: (mode: ConsoleMode) => void;
}) {
  return (
    <aside className="sticky top-0 hidden h-screen w-56 shrink-0 overflow-y-auto border-r border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3 lg:block">
      <div className="mb-4 flex items-center gap-2 px-1">
        <div className="flex size-8 items-center justify-center rounded-md bg-[rgb(var(--primary))] text-white">
          <ShieldCheck className="size-4" />
        </div>
        <div>
          <div className="text-sm font-semibold">AequorOS</div>
          <div className="text-xs text-[rgb(var(--muted-foreground))]">
            ALM Regulatory
          </div>
        </div>
      </div>
      <ModeSwitch mode={mode} onMode={onMode} />
      <nav className="flex flex-col gap-1">
        {navGroups.map((group) => (
          <div key={group.label ?? "root"}>
            {group.label ? (
              <div className="mb-1 mt-3 px-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-[rgb(var(--muted-foreground))]">
                {group.label}
              </div>
            ) : null}
            <div className="flex flex-col gap-1">
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.tab}
                    onClick={() => onTab(item.tab)}
                    className={cn(
                      "flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs text-[rgb(var(--muted-foreground))] hover:bg-[rgb(var(--muted))]",
                      activeTab === item.tab &&
                        "bg-[rgb(var(--muted))] text-[rgb(var(--foreground))]",
                    )}
                  >
                    <Icon className="size-4" />
                    {item.label}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  );
}

export function AlmTopBar({
  orgId,
  userId,
  setOrgId,
  setUserId,
  workspace,
  onBank,
  onPeriod,
  refresh,
}: {
  orgId: string;
  userId: string;
  setOrgId: (value: string) => void;
  setUserId: (value: string) => void;
  workspace: AlmWorkspace;
  onBank: (bankId: string) => void;
  onPeriod: (periodId: string) => void;
  refresh: () => void;
}) {
  return (
    <div className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-3">
      <div className="hidden text-sm font-semibold md:block">
        ALM regulatory operations
      </div>
      <div className="ml-auto flex min-w-0 items-center gap-2">
        <Input
          className="hidden w-72 md:block"
          value={orgId}
          onChange={(event) => setOrgId(event.target.value)}
          aria-label="Tenant org id"
        />
        <Input
          className="hidden w-72 xl:block"
          value={userId}
          onChange={(event) => setUserId(event.target.value)}
          aria-label="User id"
        />
        <BankPeriodSelectors
          workspace={workspace}
          onBank={onBank}
          onPeriod={onPeriod}
        />
        <Tooltip label="Refresh">
          <Button variant="outline" size="icon" onClick={refresh}>
            <RefreshCw className="size-4" />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}
