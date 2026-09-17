"use client";

import {
  useId,
  useRef,
  useState,
  type CSSProperties,
  type MouseEventHandler,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import Link from "next/link";

type DisabledWithReasonProps = {
  reason: string;
  focusable?: boolean;
  children: ReactNode | ((descriptionId: string) => ReactNode);
  className?: string;
  tooltipClassName?: string;
  placement?: "bottom" | "right";
};

export function DisabledWithReason({
  reason,
  focusable = true,
  children,
  className = "",
  tooltipClassName = "",
  placement = "bottom",
}: DisabledWithReasonProps) {
  const tooltipId = useId();
  const triggerRef = useRef<HTMLSpanElement>(null);
  const [tooltipStyle, setTooltipStyle] = useState<CSSProperties | null>(null);

  const showTooltip = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    if (placement === "right") {
      setTooltipStyle({
        left: rect.right + 8,
        top: rect.top + rect.height / 2,
        transform: "translateY(-50%)",
      });
      return;
    }
    const halfMaxWidth = 168;
    setTooltipStyle({
      left: Math.min(
        Math.max(rect.left + rect.width / 2, halfMaxWidth),
        window.innerWidth - halfMaxWidth,
      ),
      top: rect.bottom + 8,
      transform: "translateX(-50%)",
    });
  };

  return (
    <span
      ref={triggerRef}
      tabIndex={focusable ? 0 : undefined}
      aria-describedby={focusable ? tooltipId : undefined}
      className={`relative inline-flex ${className}`}
      onMouseEnter={showTooltip}
      onMouseLeave={() => setTooltipStyle(null)}
      onFocusCapture={showTooltip}
      onBlurCapture={() => setTooltipStyle(null)}
    >
      {typeof children === "function" ? children(tooltipId) : children}
      {tooltipStyle
        ? createPortal(
            <span
              id={tooltipId}
              role="tooltip"
              style={tooltipStyle}
              className={`pointer-events-none fixed z-[100] w-max max-w-80 rounded border border-white/15 bg-nav px-3 py-2 text-left text-caption font-normal normal-case leading-relaxed tracking-normal text-white shadow-pop ${tooltipClassName}`}
            >
              {reason}
            </span>,
            document.body,
          )
        : null}
    </span>
  );
}

type PermissionLinkProps = {
  href: string;
  reason?: string;
  children: ReactNode;
  className: string;
  disabledClassName?: string;
  wrapperClassName?: string;
  tooltipClassName?: string;
  placement?: "bottom" | "right";
  ariaLabel?: string;
  onClick?: MouseEventHandler<HTMLElement>;
  onMouseEnter?: MouseEventHandler<HTMLElement>;
};

export function PermissionLink({
  href,
  reason,
  children,
  className,
  disabledClassName = "",
  wrapperClassName,
  tooltipClassName,
  placement,
  ariaLabel,
  onClick,
  onMouseEnter,
}: PermissionLinkProps) {
  if (!reason) {
    return (
      <Link
        href={href}
        aria-label={ariaLabel}
        className={className}
        onClick={onClick}
        onMouseEnter={onMouseEnter}
      >
        {children}
      </Link>
    );
  }

  return (
    <DisabledWithReason
      reason={reason}
      focusable={false}
      className={wrapperClassName}
      tooltipClassName={tooltipClassName}
      placement={placement}
    >
      {(descriptionId) => (
        <span
          role="link"
          tabIndex={0}
          aria-label={ariaLabel}
          aria-disabled="true"
          aria-describedby={descriptionId}
          onMouseEnter={onMouseEnter}
          className={`${className} cursor-not-allowed ${disabledClassName}`}
        >
          {children}
        </span>
      )}
    </DisabledWithReason>
  );
}
