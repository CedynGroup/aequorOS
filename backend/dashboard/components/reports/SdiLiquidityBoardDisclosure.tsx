"use client";

import { useBankRules } from "@/lib/api/regulatoryRules";
import { useBankContext } from "@/components/shell/BankContext";

export default function SdiLiquidityBoardDisclosure({
  asOf,
  table1Breaches,
  reserveBreaches,
}: {
  asOf: string | undefined;
  table1Breaches: number | undefined;
  reserveBreaches: number | undefined;
}) {
  const { bank } = useBankContext();
  const rules = useBankRules(bank?.id);
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center text-caption text-slate">
      <p>
        SDI liquidity stress is not assessed because no BoG SDI liquidity-stress
        methodology is configured. Basel LCR/NSFR are not substituted.
      </p>
      {asOf && (
        <p className="mt-3 text-navy">
          Reserve evidence as of {asOf}: {reserveBreaches ?? 0} reserve
          breach(es).
          {rules.isInForce("SDI-LMT-MONTHLY") &&
            table1Breaches !== undefined && (
              <>
                {" "}
                LMTD Table 1 assessment: {table1Breaches} below-floor ratio(s).
              </>
            )}
        </p>
      )}
    </div>
  );
}
