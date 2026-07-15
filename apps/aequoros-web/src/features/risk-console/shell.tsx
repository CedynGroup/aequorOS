import type { CaseQueueItemRead } from "@aequoros/risk-service-api";
import {
  FileJson,
  FileText,
  History,
  LayoutDashboard,
  ListChecks,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  SlidersHorizontal,
  WalletCards,
} from "lucide-react";

import {
  Button,
  Input,
  Select,
  SelectItem,
  Tooltip,
} from "../../components/ui";
import type { ConsoleMode, ConsoleTab } from "../../lib/constants";
import { cn } from "../../lib/utils";
import { ModeSwitch } from "../alm/alm-shell";

export function Sidebar({
  activeTab,
  onTab,
  mode = "cases",
  onMode,
}: {
  activeTab: ConsoleTab;
  onTab: (tab: ConsoleTab) => void;
  mode?: ConsoleMode;
  onMode?: (mode: ConsoleMode) => void;
}) {
  const items: Array<{
    tab: ConsoleTab;
    label: string;
    icon: typeof LayoutDashboard;
  }> = [
    { tab: "overview", label: "Case Detail", icon: LayoutDashboard },
    { tab: "financial", label: "Financial Workspace", icon: WalletCards },
    { tab: "scenarios", label: "Scenarios", icon: SlidersHorizontal },
    { tab: "calculations", label: "Forecast", icon: Sparkles },
    { tab: "documents", label: "Documents", icon: FileText },
    { tab: "findings", label: "Findings", icon: ListChecks },
    { tab: "report", label: "Reports", icon: FileJson },
    { tab: "decisions", label: "Decisions", icon: History },
  ];

  return (
    <aside className="sticky top-0 hidden h-screen w-56 shrink-0 border-r border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3 lg:block">
      <div className="mb-5 flex items-center gap-2 px-1">
        <div className="flex size-8 items-center justify-center rounded-md bg-[rgb(var(--primary))] text-white">
          <ShieldCheck className="size-4" />
        </div>
        <div>
          <div className="text-sm font-semibold">AequorOS</div>
          <div className="text-xs text-[rgb(var(--muted-foreground))]">
            Risk Console
          </div>
        </div>
      </div>
      {onMode ? <ModeSwitch mode={mode} onMode={onMode} /> : null}
      <button className="mb-2 flex h-8 w-full items-center gap-2 rounded-md bg-[rgb(var(--muted))] px-2 text-left text-xs font-medium">
        <ListChecks className="size-4" />
        Case Queue
      </button>
      <nav className="flex flex-col gap-1">
        {items.map((item) => {
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
      </nav>
    </aside>
  );
}

export function TopBar({
  orgId,
  userId,
  setOrgId,
  setUserId,
  cases,
  caseId,
  chooseCase,
  refresh,
  seed,
}: {
  orgId: string;
  userId: string;
  setOrgId: (value: string) => void;
  setUserId: (value: string) => void;
  cases: CaseQueueItemRead[];
  caseId?: string;
  chooseCase: (caseId: string) => void;
  refresh: () => void;
  seed: () => void;
}) {
  return (
    <div className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-3">
      <div className="hidden text-sm font-semibold md:block">
        Risk operations
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
        <Select
          ariaLabel="Current case"
          className="w-44 max-w-[32vw] sm:w-56"
          value={caseId ?? ""}
          onValueChange={chooseCase}
          placeholder="Current case"
        >
          {cases.map((item) => (
            <SelectItem key={item.id} value={item.id} className="max-w-80">
              {item.title}
            </SelectItem>
          ))}
        </Select>
        <Tooltip label="Refresh">
          <Button variant="outline" size="icon" onClick={refresh}>
            <RefreshCw className="size-4" />
          </Button>
        </Tooltip>
        <Button variant="outline" size="sm" onClick={seed}>
          <Sparkles className="size-3.5" />
          Demo seed data
        </Button>
      </div>
    </div>
  );
}
