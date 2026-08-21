'use client';

import { useBankContext } from '@/components/shell/BankContext';
import SdiLoanBookView from '@/components/basel/SdiLoanBookView';

export default function SdiLoanBookPage() {
  const { bank } = useBankContext();
  return <SdiLoanBookView bankId={bank?.id} />;
}