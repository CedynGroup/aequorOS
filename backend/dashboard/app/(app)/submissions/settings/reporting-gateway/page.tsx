"use client";

import { useBankContext } from "@/components/shell/BankContext";
import GatewayConnection from "@/components/submissions/settings/GatewayConnection";

export default function ReportingGatewayPage() {
  const { bank } = useBankContext();
  return bank ? <GatewayConnection bankId={bank.id} /> : null;
}
