import type { ComponentPropsWithoutRef } from "react";

export default function PageContainer({
  className = "",
  ...props
}: ComponentPropsWithoutRef<"div">) {
  return <div {...props} className={`w-full max-w-6xl px-8 ${className}`} />;
}
