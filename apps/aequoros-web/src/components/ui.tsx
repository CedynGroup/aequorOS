import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Check, ChevronDown, X } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "../lib/utils";

export function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ComponentPropsWithoutRef<"button"> & {
  variant?: "default" | "outline" | "ghost" | "danger";
  size?: "default" | "sm" | "icon";
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md border text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[rgb(var(--focus))] disabled:pointer-events-none disabled:opacity-50",
        size === "default" && "h-9 px-3",
        size === "sm" && "h-8 px-2.5 text-xs",
        size === "icon" && "size-8 p-0",
        variant === "default" &&
          "border-[rgb(var(--primary))] bg-[rgb(var(--primary))] text-[rgb(var(--primary-foreground))] hover:bg-[rgb(23,69,66)]",
        variant === "outline" &&
          "border-[rgb(var(--border))] bg-[rgb(var(--surface))] text-[rgb(var(--foreground))] hover:bg-[rgb(var(--muted))]",
        variant === "ghost" &&
          "border-transparent bg-transparent text-[rgb(var(--muted-foreground))] hover:bg-[rgb(var(--muted))] hover:text-[rgb(var(--foreground))]",
        variant === "danger" &&
          "border-[rgb(var(--danger))] bg-[rgb(var(--danger))] text-white hover:bg-[rgb(137,43,36)]",
        className,
      )}
      {...props}
    />
  );
}

export function Input(props: ComponentPropsWithoutRef<"input">) {
  return (
    <input
      {...props}
      className={cn(
        "h-8 w-full rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2.5 text-sm outline-none placeholder:text-[rgb(var(--muted-foreground))] focus:border-[rgb(var(--focus))]",
        props.className,
      )}
    />
  );
}

export function Textarea(props: ComponentPropsWithoutRef<"textarea">) {
  return (
    <textarea
      {...props}
      className={cn(
        "min-h-20 w-full rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2.5 py-2 text-sm outline-none placeholder:text-[rgb(var(--muted-foreground))] focus:border-[rgb(var(--focus))]",
        props.className,
      )}
    />
  );
}

export function Label(props: ComponentPropsWithoutRef<"span">) {
  return (
    <span
      {...props}
      className={cn(
        "block text-xs font-medium uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]",
        props.className,
      )}
    />
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded-md border px-2 text-xs font-medium",
        tone === "neutral" &&
          "border-[rgb(var(--border))] bg-[rgb(var(--muted))] text-[rgb(var(--muted-foreground))]",
        tone === "success" &&
          "border-emerald-200 bg-emerald-50 text-emerald-800",
        tone === "warning" && "border-amber-200 bg-amber-50 text-amber-800",
        tone === "danger" && "border-red-200 bg-red-50 text-red-800",
        tone === "info" && "border-cyan-200 bg-cyan-50 text-cyan-800",
      )}
    >
      {children}
    </span>
  );
}

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))]",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  actions,
  meta,
}: {
  title: string;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="flex min-h-11 items-center justify-between gap-3 border-b border-[rgb(var(--border))] px-3 py-2">
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold">{title}</h2>
        {meta ? <div className="mt-0.5 text-xs text-[rgb(var(--muted-foreground))]">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Alert({
  title,
  children,
  tone = "neutral",
}: {
  title: string;
  children?: ReactNode;
  tone?: "neutral" | "danger" | "warning";
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        tone === "neutral" &&
          "border-[rgb(var(--border))] bg-[rgb(var(--muted))]",
        tone === "danger" && "border-red-200 bg-red-50 text-red-900",
        tone === "warning" && "border-amber-200 bg-amber-50 text-amber-900",
      )}
    >
      <div className="font-medium">{title}</div>
      {children ? <div className="mt-1 text-xs opacity-80">{children}</div> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-[rgb(var(--muted))]", className)} />;
}

export const Tabs = TabsPrimitive.Root;
export const TabsList = TabsPrimitive.List;
export const TabsContent = TabsPrimitive.Content;

export function TabsTrigger({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "h-8 rounded-md px-2.5 text-xs font-medium text-[rgb(var(--muted-foreground))] outline-none data-[state=active]:bg-[rgb(var(--surface))] data-[state=active]:text-[rgb(var(--foreground))] data-[state=active]:shadow-sm",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  ariaLabel,
  className,
  value,
  onValueChange,
  placeholder,
  children,
  disabled = false,
}: {
  ariaLabel?: string;
  className?: string;
  value: string;
  onValueChange: (value: string) => void;
  placeholder: string;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <SelectPrimitive.Root
      value={value}
      onValueChange={onValueChange}
      disabled={disabled}
    >
      <SelectPrimitive.Trigger
        aria-label={ariaLabel ?? placeholder}
        className={cn(
          "inline-flex h-8 min-w-36 items-center justify-between gap-2 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2.5 text-sm outline-none focus:border-[rgb(var(--focus))] disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon>
          <ChevronDown className="size-3.5" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content className="z-50 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-1 shadow-lg">
          <SelectPrimitive.Viewport>{children}</SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

export function SelectItem({
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "relative flex h-8 cursor-default select-none items-center rounded px-2.5 pl-7 text-sm outline-none data-[highlighted]:bg-[rgb(var(--muted))]",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemIndicator className="absolute left-2">
        <Check className="size-3.5" />
      </SelectPrimitive.ItemIndicator>
      <SelectPrimitive.ItemText>
        <span className="block truncate">{children}</span>
      </SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

export function Checkbox({
  checked,
  onCheckedChange,
  "aria-label": ariaLabel,
}: {
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean) => void;
  "aria-label": string;
}) {
  return (
    <CheckboxPrimitive.Root
      aria-label={ariaLabel}
      checked={checked}
      onCheckedChange={(value) => onCheckedChange(value === true)}
      className="flex size-4 items-center justify-center rounded border border-[rgb(var(--border))] bg-[rgb(var(--surface))] data-[state=checked]:border-[rgb(var(--primary))] data-[state=checked]:bg-[rgb(var(--primary))]"
    >
      <CheckboxPrimitive.Indicator>
        <Check className="size-3 text-white" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export function Switch({
  checked,
  onCheckedChange,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <SwitchPrimitive.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      className="relative h-5 w-9 rounded-full bg-slate-300 outline-none data-[state=checked]:bg-[rgb(var(--primary))]"
    >
      <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
    </SwitchPrimitive.Root>
  );
}

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({
  children,
  description,
  title,
}: {
  children: ReactNode;
  description?: string;
  title: string;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/30" />
      <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 w-[min(560px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-0 shadow-xl">
        <div className="flex h-12 items-center justify-between border-b border-[rgb(var(--border))] px-4">
          <DialogPrimitive.Title className="text-sm font-semibold">{title}</DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="sr-only">
              {description}
            </DialogPrimitive.Description>
          ) : null}
          <DialogPrimitive.Close className="rounded p-1 hover:bg-[rgb(var(--muted))]">
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export const DropdownMenu = DropdownMenuPrimitive.Root;
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
export const DropdownMenuContent = DropdownMenuPrimitive.Content;
export const DropdownMenuItem = DropdownMenuPrimitive.Item;

export function Tooltip({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <TooltipPrimitive.Provider>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content className="z-50 rounded bg-slate-950 px-2 py-1 text-xs text-white" sideOffset={6}>
            {label}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
